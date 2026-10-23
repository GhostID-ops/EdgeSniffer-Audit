"""Loss-aware transaction identity/accounting view. No quotes or order submission."""
import base64
import hashlib
import json

PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
TRADE = bytes([189, 219, 127, 211, 78, 230, 97, 238])
EVENT_CPI = bytes.fromhex('e445a52e51cb9a1d')
SOL_QUOTES = {'11111111111111111111111111111111', 'So11111111111111111111111111111111111111112'}


def b58decode(value):
    n = 0
    for c in value:
        n = n*58 + ALPHABET.index(c)
    return b'\0' * (len(value)-len(value.lstrip('1'))) + n.to_bytes((n.bit_length()+7)//8, 'big')


def b58encode(value):
    n, out = int.from_bytes(value, 'big'), ''
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r]+out
    return '1'*(len(value)-len(value.lstrip(b'\0'))) + out


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def trade_prefix(data):
    # Stable TradeEvent prefix only; unknown fee/extensions kept in raw payload.
    if len(data) < 129 or data[:8] != TRADE or data[56] not in (0, 1):
        return None
    event = {'mint': b58encode(data[8:40]),
            'legacy_sol_amount_field': int.from_bytes(data[40:48], 'little'),
            'sol_amount_lamports': None,
            'token_amount_raw': int.from_bytes(data[48:56], 'little'),
            'side': 'buy' if data[56] else 'sell', 'user': b58encode(data[57:89]),
            'chain_event_timestamp_seconds': int.from_bytes(data[89:97], 'little', signed=True),
            'virtual_sol_reserves': int.from_bytes(data[97:105], 'little'),
            'virtual_token_reserves': int.from_bytes(data[105:113], 'little'),
            'real_sol_reserves': int.from_bytes(data[113:121], 'little'),
            'real_token_reserves': int.from_bytes(data[121:129], 'little'),
            'raw_event_base64': base64.b64encode(data).decode(),
            'fee_layout_decoded': False, 'quote_layout_decoded': False}
    # Reuse the existing exactCurveEconomicsV1 TradeEvent field order, keeping
    # unsupported extension bytes opaque. A stable prefix alone is NOT SOL flow.
    offset = 129
    def take(n):
        nonlocal offset
        if n < 0 or offset+n > len(data):
            raise ValueError('short event')
        value=data[offset:offset+n];offset+=n;return value
    def number(n=8): return int.from_bytes(take(n),'little')
    try:
        event['fee_recipient']=b58encode(take(32));event['protocol_fee_bps']=number();event['protocol_fee_amount']=number()
        event['creator']=b58encode(take(32));event['creator_fee_bps']=number();event['creator_fee_amount']=number()
        if number(1) not in (0,1):raise ValueError('invalid bool')
        take(32)  # volume accounting and last update timestamp
        length=number(4)
        if length>128:raise ValueError('invalid instruction name')
        event['instruction_name']=take(length).decode('utf-8')
        mayhem=number(1)
        if mayhem not in (0,1):raise ValueError('invalid bool')
        event['mayhem']=bool(mayhem)
        for name in ('cashback_bps','cashback_amount','buyback_bps','buyback_amount'):event[name]=number()
        shareholders=number(4)
        if shareholders>256:raise ValueError('invalid shareholders')
        take(shareholders*34)
        event['quote_mint']=b58encode(take(32));event['quote_amount_raw']=number()
        event['virtual_quote_reserves']=number();event['real_quote_reserves']=number()
        if len(data)-offset not in (0,16,32):raise ValueError('unsupported tail')
        event['opaque_extension_base64']=base64.b64encode(take(len(data)-offset)).decode()
        event['quote_layout_decoded']=True
        if event['quote_mint'] in SOL_QUOTES and event['legacy_sol_amount_field'] in (0,event['quote_amount_raw']):
            event['sol_amount_lamports']=event['quote_amount_raw']
        # Fee fields retained, not certified as the complete future quote schedule.
    except (ValueError, UnicodeError):
        event['quote_layout_decoded']=False
        event['sol_amount_lamports']=None
    return event


def normalize(tx, expected_signature=None):
    gaps = []
    body = tx.get('transaction') or {}
    message = body.get('message') or {}
    meta = tx.get('meta')
    status = 'UNKNOWN' if not isinstance(meta, dict) or 'err' not in meta else 'SUCCESS' if meta['err'] is None else 'FAILED'
    if status == 'SUCCESS' and not (isinstance(meta.get('preBalances'), list) and meta['preBalances']
                                   and isinstance(meta.get('postBalances'), list) and meta['postBalances']):
        status = 'UNKNOWN'
        gaps.append('empty_metadata_not_success_proof')
    if status == 'UNKNOWN':
        gaps.append('execution_status_unknown')
    meta = meta if isinstance(meta, dict) else {}
    signatures = body.get('signatures') or []
    signature = signatures[0] if signatures else tx.get('signature')
    if not signature or (expected_signature and signature != expected_signature):
        gaps.append('signature_missing_or_mismatch')
        status = 'UNKNOWN'
    if tx.get('signature') and signature != tx['signature']:
        gaps.append('signature_header_mismatch')
        status = 'UNKNOWN'
    static = message.get('accountKeys') or []
    if not all(isinstance(k, str) for k in static):
        static = []
        gaps.append('unsupported_parsed_accounts')
    loaded = meta.get('loadedAddresses') or {}
    writable, readonly = loaded.get('writable') or [], loaded.get('readonly') or []
    keys = static + writable + readonly
    lookups = message.get('addressTableLookups') or []
    expected_w = sum(len(x.get('writableIndexes') or []) for x in lookups)
    expected_r = sum(len(x.get('readonlyIndexes') or []) for x in lookups)
    if expected_w != len(writable) or expected_r != len(readonly):
        gaps.append('alt_resolution_count_mismatch')
    instructions = []
    outer = message.get('instructions')
    if not isinstance(outer, list):
        gaps.append('outer_instructions_missing')
        outer = []
    inner = meta.get('innerInstructions')
    if not isinstance(inner, list):
        gaps.append('inner_context_unrecorded')
        inner = []
    groups = [(i, None, ix) for i, ix in enumerate(outer)]
    for group in inner:
        parent = group.get('index')
        if type(parent) is not int or not 0 <= parent < len(outer):
            gaps.append('invalid_inner_parent')
        groups.extend((parent, i, ix) for i, ix in enumerate(group.get('instructions') or []))
    for parent, child, ix in groups:
        ids = [ix.get('programIdIndex'), *(ix.get('accounts') or [])]
        valid = all(type(i) is int and 0 <= i < len(keys) for i in ids)
        if not valid:
            gaps.append('unresolved_instruction_account')
        try:
            raw = b58decode(ix.get('data', ''))
            data_valid = 'data' in ix
        except (ValueError, TypeError):
            raw, data_valid = b'', False
        if not data_valid:
            gaps.append('invalid_instruction_data')
        instructions.append({'outer_index': parent, 'inner_index': child,
                             'stack_height': ix.get('stackHeight'),
                             'program_id': keys[ids[0]] if valid else None,
                             'account_indices': ix.get('accounts'),
                             'accounts': [keys[i] for i in ids[1:]] if valid else None,
                             'data_base58': ix.get('data'), 'discriminator_hex': raw[:8].hex(),
                             'identity_resolved': valid and data_valid})
    events = []
    # Only runtime-attributed Pump log data, not arbitrary matching strings.
    stack = []
    logs = meta.get('logMessages')
    if not isinstance(logs, list):
        gaps.append('logs_unrecorded')
        logs = []
    for i, line in enumerate(logs):
        if line.startswith('Program ') and ' invoke [' in line:
            parts = line.split()
            try:
                depth = int(parts[-1].strip('[]'))
                if depth != len(stack)+1:
                    stack = []
                    gaps.append('log_stack_discontinuity')
                stack.append(parts[1])
            except (ValueError, IndexError):
                stack = []
        elif line.startswith('Program ') and (' success' in line or ' failed:' in line):
            program = line.split()[1]
            if stack and stack[-1] == program:
                stack.pop()
            else:
                stack = []
        elif line.startswith('Program data: ') and stack and stack[-1] == PUMP:
            try:
                event = trade_prefix(base64.b64decode(line[14:], validate=True))
            except (ValueError, TypeError):
                event = None
            if event:
                event.update(log_index=i, execution_status=status, instruction_coordinates=None,
                             realized_swap_amounts_eligible=status == 'SUCCESS' and event['quote_layout_decoded'])
                events.append(event)
    if stack:
        gaps.append('unclosed_log_stack')
    # Anchor event-CPI payloads provide exact outer/inner coordinates. Reconcile
    # logs one-to-one; identical repeated events remain separate, never set-dedup.
    matched_logs=set()
    for instruction in instructions:
        if instruction['program_id'] != PUMP or instruction['inner_index'] is None:
            continue
        raw=b58decode(instruction['data_base58']) if instruction['data_base58'] else b''
        if not raw.startswith(EVENT_CPI):continue
        event=trade_prefix(raw[8:])
        if not event:continue
        coords={'outer_index':instruction['outer_index'],'inner_index':instruction['inner_index'],
                'stack_height':instruction['stack_height']}
        matching=next((i for i,e in enumerate(events) if i not in matched_logs and
                       e['raw_event_base64']==event['raw_event_base64']),None)
        if matching is not None:
            events[matching]['instruction_coordinates']=coords;matched_logs.add(matching)
        else:
            event.update(log_index=None,execution_status=status,instruction_coordinates=coords,
                         realized_swap_amounts_eligible=status=='SUCCESS' and event['quote_layout_decoded'])
            events.append(event);matched_logs.add(len(events)-1)
    for event in events:
        event['signed_sol_flow_eligible']=event['realized_swap_amounts_eligible'] and event['sol_amount_lamports'] is not None
    pre, post = meta.get('preBalances'), meta.get('postBalances')
    balances = []
    if isinstance(pre, list) and isinstance(post, list) and len(pre) == len(post) == len(keys) and all(
            type(v) is int and v >= 0 for v in pre+post):
        balances = [{'account_index': i, 'account': k, 'pre_lamports': a, 'post_lamports': b,
                     'net_delta_lamports': b-a} for i, (k, a, b) in enumerate(zip(keys, pre, post))]
    else:
        gaps.append('lamport_balance_coverage_missing')
    token_rows = []
    before, after = meta.get('preTokenBalances'), meta.get('postTokenBalances')
    if not isinstance(before, list) or not isinstance(after, list):
        gaps.append('token_balance_coverage_missing')
    else:
        a = {(x.get('accountIndex'), x.get('mint')): x for x in before}
        b = {(x.get('accountIndex'), x.get('mint')): x for x in after}
        for key in sorted(a.keys() | b.keys(), key=str):
            left, right = a.get(key), b.get(key)
            try:
                delta = int(right['uiTokenAmount']['amount'])-int(left['uiTokenAmount']['amount']) if left and right else None
            except (KeyError, ValueError, TypeError):
                delta = None
            token_rows.append({'account_index': key[0], 'mint': key[1], 'pre': left, 'post': right,
                               'net_delta_raw': delta})
    return {'schema': 'TRANSACTION_EVIDENCE_VIEW_V1', 'signature': signature, 'slot': tx.get('slot'),
            'transaction_index': tx.get('transactionIndex'), 'chain_block_time_seconds': tx.get('blockTime'),
            'execution_status': status, 'execution_error': meta.get('err'), 'fee_lamports': meta.get('fee'),
            'resolved_account_keys': keys, 'address_table_lookups': lookups, 'instructions': instructions,
            'pump_trade_event_prefixes': events, 'lamport_balance_deltas': balances,
            'token_balance_deltas': token_rows, 'gaps': sorted(set(gaps)),
            'pump_instruction_count': sum(x['program_id'] == PUMP for x in instructions),
            'instruction_identity_complete': bool(instructions) and all(x['identity_resolved'] for x in instructions) and not any(
                x in gaps for x in ('alt_resolution_count_mismatch', 'invalid_inner_parent', 'inner_context_unrecorded')),
            'amount_interpretation': 'Transaction balance changes include fees/rent/transfers; not automatically swap amounts.',
            'economic_admission': False}
