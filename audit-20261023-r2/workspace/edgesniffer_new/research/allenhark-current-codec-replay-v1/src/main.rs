use anyhow::{bail, Context, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};
use solana_entry::entry::Entry;
use solana_ledger::shred::{merkle::recover_for_replay, Shred, Shredder};
use std::{collections::{BTreeMap, BTreeSet, HashMap, HashSet}, env, fs::File, io::{Read, Write}, path::PathBuf, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};

const UDP_PORT: u16 = 20_000;

#[derive(Debug, Clone)]
struct Packet {
    payload: Vec<u8>,
}

#[derive(Debug, Serialize, Default)]
struct Report {
    tool: &'static str,
    codec: &'static str,
    jito_revision: &'static str,
    input_pcap: String,
    input_sha256: String,
    target: String,
    linktype: u32,
    packets_seen: usize,
    udp20000_packets: usize,
    unique_shred_payloads: usize,
    duplicate_payloads: usize,
    malformed_packets: usize,
    parsed_shreds: usize,
    shred_parse_errors: usize,
    data_shreds: usize,
    coding_shreds: usize,
    slots: BTreeSet<u64>,
    complete_payloads_attempted: usize,
    deshred_successes: usize,
    deshred_failures: usize,
    wincode_entry_decode_successes: usize,
    wincode_entry_decode_failures: usize,
    decoded_entries: usize,
    decoded_transactions: usize,
    target_transactions: usize,
    target_fee_payer_transactions: usize,
    target_signature_samples: Vec<TargetSignature>,
    slot_summaries: Vec<SlotSummary>,
    failure_samples: Vec<FailureSample>,
}

#[derive(Debug, Serialize)]
struct SlotSummary {
    slot: u64,
    data_shreds: usize,
    coding_shreds: usize,
    completed_ranges: usize,
    deshred_successes: usize,
    wincode_successes: usize,
    entries: usize,
    transactions: usize,
    target_transactions: usize,
    target_fee_payer_transactions: usize,
}

#[derive(Debug, Serialize)]
struct FailureSample {
    slot: u64,
    start_index: u32,
    end_index: u32,
    stage: &'static str,
    error: String,
    payload_len: usize,
    payload_sha256: String,
    prefix_hex: String,
}

#[derive(Debug, Serialize)]
struct TargetSignature {
    slot: u64,
    signature: String,
    fee_payer_match: bool,
}

#[derive(Debug)]
struct Args {
    pcap: PathBuf,
    out: PathBuf,
    target: String,
    live_interface: Option<String>,
    duration_secs: u64,
    receipt_out: Option<PathBuf>,
    target_transaction_out: Option<PathBuf>,
}

fn main() -> Result<()> {
    let args = parse_args()?;
    if let Some(interface) = args.live_interface.as_deref() {
        return run_live(&args, interface);
    }
    let mut input = Vec::new();
    File::open(&args.pcap)
        .with_context(|| format!("open {}", args.pcap.display()))?
        .read_to_end(&mut input)?;
    let input_sha256 = sha256_hex(&input);
    let (linktype, raw_packets) = read_pcap(&input)?;

    let mut report = Report {
        tool: "allenhark-current-codec-replay-v1",
        codec: "current-jito-merkle-shred + wincode Entry",
        jito_revision: "58704cca75f4139cbab4778a45b6b81eff3cc5db",
        input_pcap: args.pcap.display().to_string(),
        input_sha256,
        target: args.target.clone(),
        linktype,
        packets_seen: raw_packets.len(),
        ..Report::default()
    };

    let mut unique = HashMap::<String, Vec<u8>>::new();
    for packet in raw_packets {
        match extract_udp_payload(&packet.payload, linktype) {
            Ok(Some(payload)) => {
                report.udp20000_packets += 1;
                let digest = sha256_hex(&payload);
                if unique.insert(digest, payload).is_some() {
                    report.duplicate_payloads += 1;
                }
            }
            Ok(None) => {}
            Err(_) => report.malformed_packets += 1,
        }
    }
    report.unique_shred_payloads = unique.len();

    let mut slots = BTreeMap::<u64, SlotData>::new();
    for payload in unique.into_values() {
        match Shred::new_from_serialized_shred(payload) {
            Ok(shred) => {
                report.parsed_shreds += 1;
                let slot = shred.slot();
                report.slots.insert(slot);
                let state = slots.entry(slot).or_default();
                if shred.is_data() {
                    report.data_shreds += 1;
                    state.data.insert(shred.index(), shred);
                } else {
                    report.coding_shreds += 1;
                    state.coding += 1;
                }
            }
            Err(_) => report.shred_parse_errors += 1,
        }
    }

    for (slot, state) in slots {
        let mut summary = SlotSummary {
            slot,
            data_shreds: state.data.len(),
            coding_shreds: state.coding,
            completed_ranges: 0,
            deshred_successes: 0,
            wincode_successes: 0,
            entries: 0,
            transactions: 0,
            target_transactions: 0,
            target_fee_payer_transactions: 0,
        };
        let mut range = Vec::<Shred>::new();
        let mut previous_index = None;
        for (index, shred) in state.data {
            if previous_index.is_some_and(|previous| previous + 1 != index) {
                range.clear();
            }
            range.push(shred);
            previous_index = Some(index);
            if !range.last().expect("range is non-empty").data_complete() {
                continue;
            }

            report.complete_payloads_attempted += 1;
            summary.completed_ranges += 1;
            let start_index = range.first().expect("range is non-empty").index();
            let end_index = index;
            let payloads: Vec<Vec<u8>> = range.iter().map(|shred| shred.payload().as_ref().to_vec()).collect();
            let payload = match Shredder::deshred(payloads) {
                Ok(payload) => {
                    report.deshred_successes += 1;
                    summary.deshred_successes += 1;
                    payload
                }
                Err(error) => {
                    report.deshred_failures += 1;
                    push_failure(&mut report, slot, start_index, end_index, "deshred", error.to_string(), &[]);
                    range.clear();
                    continue;
                }
            };

            match wincode::deserialize::<Vec<Entry>>(&payload) {
                Ok(entries) => {
                    report.wincode_entry_decode_successes += 1;
                    summary.wincode_successes += 1;
                    let transactions: usize = entries.iter().map(|entry| entry.transactions.len()).sum();
                    let mut target_transactions = 0;
                    let mut target_fee_payer_transactions = 0;
                    for entry in &entries {
                        for transaction in &entry.transactions {
                            let keys = transaction.message.static_account_keys();
                            let target_in_keys = keys.iter().any(|key| key.to_string() == args.target);
                            if target_in_keys {
                                target_transactions += 1;
                            }
                            let fee_payer_match = keys.first().is_some_and(|key| key.to_string() == args.target);
                            if fee_payer_match {
                                target_fee_payer_transactions += 1;
                                for signature in transaction.signatures.iter().take(1) {
                                    report.target_signature_samples.push(TargetSignature {
                                        slot,
                                        signature: signature.to_string(),
                                        fee_payer_match,
                                    });
                                }
                            }
                        }
                    }
                    report.decoded_entries += entries.len();
                    report.decoded_transactions += transactions;
                    report.target_transactions += target_transactions;
                    report.target_fee_payer_transactions += target_fee_payer_transactions;
                    summary.entries += entries.len();
                    summary.transactions += transactions;
                    summary.target_transactions += target_transactions;
                    summary.target_fee_payer_transactions += target_fee_payer_transactions;
                }
                Err(error) => {
                    report.wincode_entry_decode_failures += 1;
                    push_failure(&mut report, slot, start_index, end_index, "wincode_entry", error.to_string(), &payload);
                }
            }
            range.clear();
        }
        report.slot_summaries.push(summary);
    }

    let mut file = File::create(&args.out).with_context(|| format!("create {}", args.out.display()))?;
    serde_json::to_writer_pretty(&mut file, &report)?;
    file.write_all(b"\n")?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

#[derive(Default)]
struct SlotData {
    data: BTreeMap<u32, Shred>,
    coding: usize,
}

fn parse_args() -> Result<Args> {
    let mut pcap = None;
    let mut out = None;
    let mut target = None;
    let mut live_interface = None;
    let mut duration_secs = 0;
    let mut receipt_out = None;
    let mut target_transaction_out = None;
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--pcap" => pcap = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--target" => target = args.next(),
            "--live-interface" => live_interface = args.next(),
            "--duration-secs" => duration_secs = args.next().context("missing --duration-secs value")?.parse().context("invalid --duration-secs")?,
            "--receipt-out" => receipt_out = args.next().map(PathBuf::from),
            "--target-transaction-out" => target_transaction_out = args.next().map(PathBuf::from),
            _ => bail!("unknown argument {arg}"),
        }
    }
    if live_interface.is_none() && pcap.is_none() {
        bail!("missing --pcap")
    }
    if live_interface.is_none() && out.is_none() {
        bail!("missing --out")
    }
    Ok(Args {
        pcap: pcap.unwrap_or_default(),
        out: out.unwrap_or_default(),
        target: target.context("missing --target")?,
        live_interface,
        duration_secs,
        receipt_out,
        target_transaction_out,
    })
}

#[derive(Default)]
struct LiveStats {
    packets_seen: usize,
    udp20000_packets: usize,
    unique_payloads: usize,
    parsed_shreds: usize,
    shred_parse_errors: usize,
    data_shreds: usize,
    coding_shreds: usize,
    fec_sets_seen: usize,
    fec_recovery_attempts: usize,
    fec_recovery_successes: usize,
    fec_recovery_failures: usize,
    recovered_shreds: usize,
    complete_ranges: usize,
    deshred_successes: usize,
    deshred_failures: usize,
    wincode_successes: usize,
    wincode_failures: usize,
    decoded_entries: usize,
    decoded_transactions: usize,
    target_transactions: usize,
    target_fee_payer_transactions: usize,
}

#[derive(Default)]
struct LiveSlot {
    data: BTreeMap<u32, Shred>,
    receive_clocks: BTreeMap<u32, LiveClock>,
    fec_sets: HashMap<u32, LiveFecSet>,
}

#[derive(Default)]
struct LiveFecSet {
    shreds: HashMap<solana_ledger::shred::ShredId, Shred>,
    recovered: bool,
}

#[derive(Clone, Copy)]
struct LiveClock {
    unix_ns: u128,
    monotonic_ns: u128,
    source: &'static str,
}

fn run_live(args: &Args, interface: &str) -> Result<()> {
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (args, interface);
        bail!("--live-interface is supported only on Linux")
    }
    #[cfg(target_os = "linux")]
    {
        let receipt_path = args.receipt_out.clone().context("live mode requires --receipt-out")?;
        if args.duration_secs == 0 {
            eprintln!("current-codec live shadow listening on {interface}; duration=unbounded");
        }
        let mut receipts = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&receipt_path)
            .with_context(|| format!("open {}", receipt_path.display()))?;
        let mut target_transactions = if let Some(path) = args.target_transaction_out.clone() {
            Some(std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .with_context(|| format!("open {}", path.display()))?)
        } else {
            None
        };
        let health_path = receipt_path.with_extension("health.json");
        let fd = open_packet_socket(interface)?;
        let started = Instant::now();
        let mut next_health = Instant::now();
        let mut buffer = [0u8; 2048];
        let mut stats = LiveStats::default();
        let mut unique_payloads = HashSet::<String>::new();
        let mut slots = BTreeMap::<u64, LiveSlot>::new();
        let mut decoded_ranges = BTreeSet::<(u64, u32)>::new();

        loop {
            if args.duration_secs > 0 && started.elapsed() >= Duration::from_secs(args.duration_secs) {
                break;
            }
            let received = unsafe { libc::recv(fd, buffer.as_mut_ptr().cast(), buffer.len(), 0) };
            if received <= 0 {
                continue;
            }
            stats.packets_seen += 1;
            let packet_receive_unix_ns = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos();
            let packet_receive_monotonic_ns = started.elapsed().as_nanos();
            let packet = &buffer[..received as usize];
            let Ok(Some(payload)) = extract_udp_payload(packet, 1) else { continue; };
            stats.udp20000_packets += 1;
            if !unique_payloads.insert(sha256_hex(&payload)) { continue; }
            stats.unique_payloads += 1;
            let Ok(shred) = Shred::new_from_serialized_shred(payload) else {
                stats.shred_parse_errors += 1;
                continue;
            };
            stats.parsed_shreds += 1;
            let slot = shred.slot();
            let is_data = shred.is_data();
            if is_data { stats.data_shreds += 1; } else { stats.coding_shreds += 1; }
            let slot_state = slots.entry(slot).or_default();
            let fec_set_index = shred.fec_set_index();
            let shred_id = shred.id();
            let mut recovered_data = Vec::new();
            {
                let fec_set = slot_state.fec_sets.entry(fec_set_index).or_default();
                if fec_set.shreds.is_empty() { stats.fec_sets_seen += 1; }
                if fec_set.shreds.insert(shred_id, shred.clone()).is_none() {
                    recovered_data = recover_live_fec_set(fec_set, &mut stats);
                }
            }
            if is_data {
                let shred_index = shred.index();
                slot_state.data.insert(shred_index, shred);
                slot_state.receive_clocks.insert(shred_index, LiveClock {
                    unix_ns: packet_receive_unix_ns,
                    monotonic_ns: packet_receive_monotonic_ns,
                    source: "CURRENT_CODEC_AF_PACKET_RECEIVE",
                });
            }
            for recovered in recovered_data {
                if recovered.is_data() {
                    let recovered_index = recovered.index();
                    slot_state.data.insert(recovered_index, recovered);
                    slot_state.receive_clocks.insert(recovered_index, LiveClock {
                        unix_ns: packet_receive_unix_ns,
                        monotonic_ns: packet_receive_monotonic_ns,
                        source: "CURRENT_CODEC_AF_PACKET_FEC_RECOVERY_OBSERVATION",
                    });
                }
            }
            let min_slot = slot.saturating_sub(128);
            slots.retain(|existing_slot, _| *existing_slot >= min_slot);
            if let Some(state) = slots.get(&slot) {
                decode_live_ranges(slot, &state.data, &state.receive_clocks, &mut decoded_ranges, &args.target, &mut receipts, &mut target_transactions, &mut stats)?;
            }
            if next_health.elapsed() >= Duration::from_secs(5) {
                write_live_health(&health_path, interface, &stats, started.elapsed(), &receipt_path)?;
                next_health = Instant::now();
            }
        }
        write_live_health(&health_path, interface, &stats, started.elapsed(), &receipt_path)?;
        if let Some(file) = target_transactions.as_mut() {
            file.flush()?;
        }
        unsafe { libc::close(fd); }
        println!("{}", serde_json::to_string_pretty(&live_health_json(interface, &stats, started.elapsed(), &receipt_path))?);
        Ok(())
    }
}

fn recover_live_fec_set(fec_set: &mut LiveFecSet, stats: &mut LiveStats) -> Vec<Shred> {
    // Current Jito uses fixed 32 data + 32 coding shards. Recovery is
    // attempted as soon as any 32 distinct shards are available.
    if fec_set.recovered || fec_set.shreds.len() < 32 {
        return Vec::new();
    }
    stats.fec_recovery_attempts += 1;
    let input: Vec<Shred> = fec_set.shreds.values().cloned().collect();
    match recover_for_replay(input) {
        Ok(recovered) => {
            let mut recovered_data = Vec::new();
            for item in recovered {
                let Ok(shred) = item else { continue; };
                if fec_set.shreds.insert(shred.id(), shred.clone()).is_none() {
                    stats.recovered_shreds += 1;
                    if shred.is_data() {
                        recovered_data.push(shred);
                    }
                }
            }
            fec_set.recovered = true;
            stats.fec_recovery_successes += 1;
            recovered_data
        }
        Err(_) => {
            stats.fec_recovery_failures += 1;
            Vec::new()
        }
    }
}

#[cfg(target_os = "linux")]
fn open_packet_socket(interface: &str) -> Result<i32> {
    let name = std::ffi::CString::new(interface).context("invalid interface name")?;
    let ifindex = unsafe { libc::if_nametoindex(name.as_ptr()) };
    if ifindex == 0 { bail!("interface {interface} not found") }
    let protocol = (libc::ETH_P_ALL as u16).to_be() as i32;
    let fd = unsafe { libc::socket(libc::AF_PACKET, libc::SOCK_RAW, protocol) };
    if fd < 0 { bail!("AF_PACKET socket failed: {}", std::io::Error::last_os_error()) }
    let timeout = libc::timeval { tv_sec: 1, tv_usec: 0 };
    let timeout_ptr = &timeout as *const libc::timeval as *const libc::c_void;
    let timeout_len = std::mem::size_of::<libc::timeval>() as libc::socklen_t;
    unsafe {
        libc::setsockopt(fd, libc::SOL_SOCKET, libc::SO_RCVTIMEO, timeout_ptr, timeout_len);
        let mut address: libc::sockaddr_ll = std::mem::zeroed();
        address.sll_family = libc::AF_PACKET as u16;
        address.sll_protocol = (libc::ETH_P_ALL as u16).to_be();
        address.sll_ifindex = ifindex as i32;
        let result = libc::bind(fd, (&address as *const libc::sockaddr_ll).cast(), std::mem::size_of::<libc::sockaddr_ll>() as libc::socklen_t);
        if result < 0 {
            libc::close(fd);
            bail!("bind AF_PACKET to {interface} failed: {}", std::io::Error::last_os_error())
        }
    }
    Ok(fd)
}

fn decode_live_ranges(slot: u64, data: &BTreeMap<u32, Shred>, receive_clocks: &BTreeMap<u32, LiveClock>, decoded: &mut BTreeSet<(u64, u32)>, target: &str, receipts: &mut File, target_transactions: &mut Option<File>, stats: &mut LiveStats) -> Result<()> {
    let mut range = Vec::<&Shred>::new();
    let mut previous = None;
    for (index, shred) in data {
        if previous.is_some_and(|value| value + 1 != *index) {
            range.clear();
        }
        range.push(shred);
        previous = Some(*index);
        if !shred.data_complete() { continue; }
        if decoded.contains(&(slot, *index)) { range.clear(); continue; }
        stats.complete_ranges += 1;
        let payloads: Vec<Vec<u8>> = range.iter().map(|item| item.payload().as_ref().to_vec()).collect();
        let mut receive_clock: Option<LiveClock> = None;
        let mut has_recovery_observation = false;
        for item in &range {
            if let Some(clock) = receive_clocks.get(&item.index()) {
                has_recovery_observation |= clock.source.contains("FEC_RECOVERY");
                receive_clock = Some(match receive_clock {
                    Some(current) if current.monotonic_ns >= clock.monotonic_ns => current,
                    _ => *clock,
                });
            }
        }
        let payload = match Shredder::deshred(payloads) {
            Ok(payload) => { stats.deshred_successes += 1; payload }
            Err(_) => { stats.deshred_failures += 1; range.clear(); continue; }
        };
        let decoded_ok = match wincode::deserialize::<Vec<Entry>>(&payload) {
            Ok(entries) => {
                stats.wincode_successes += 1;
                stats.decoded_entries += entries.len();
                for entry in &entries {
                    stats.decoded_transactions += entry.transactions.len();
                    for transaction in &entry.transactions {
                        let keys = transaction.message.static_account_keys();
                        let in_keys = keys.iter().any(|key| key.to_string() == target);
                        let fee_payer = keys.first().is_some_and(|key| key.to_string() == target);
                        if in_keys { stats.target_transactions += 1; }
                        if fee_payer {
                            stats.target_fee_payer_transactions += 1;
                            for signature in transaction.signatures.iter().take(1) {
                                let account_keys: Vec<String> = keys.iter().map(|key| key.to_string()).collect();
                                let instructions: Vec<serde_json::Value> = transaction.message.instructions().iter().map(|instruction| {
                                    serde_json::json!({
                                        "program_id_index": instruction.program_id_index,
                                        "program_id": account_keys.get(instruction.program_id_index as usize),
                                        "accounts": instruction.accounts,
                                        "data_hex": hex::encode(&instruction.data),
                                    })
                                }).collect();
                                if let Some(target_output) = target_transactions.as_mut() {
                                    writeln!(target_output, "{}", serde_json::json!({
                                        "schema": "ALLENHARK_CURRENT_CODEC_TARGET_TRANSACTION_V1",
                                        "slot": slot,
                                        "signature": signature.to_string(),
                                        "wallet": target,
                                        "fee_payer": true,
                                        "native_receive_unix_ns": receive_clock.map(|clock| clock.unix_ns),
                                        "native_receive_monotonic_ns": receive_clock.map(|clock| clock.monotonic_ns.to_string()),
                                        "native_receive_source": if has_recovery_observation { "CURRENT_CODEC_AF_PACKET_FEC_RECOVERY_OBSERVATION" } else { "CURRENT_CODEC_AF_PACKET_RECEIVE" },
                                        "native_receive_slot": slot,
                                        "account_keys": account_keys,
                                        "instructions": instructions,
                                    }))?;
                                }
                                writeln!(receipts, "{}", serde_json::json!({
                                    "schema":"ALLENHARK_CURRENT_CODEC_TARGET_RECEIPT_V2",
                                    "slot":slot,
                                    "signature":signature.to_string(),
                                    "wallet":target,
                                    "fee_payer":true,
                                    "native_receive_unix_ns":receive_clock.map(|clock| clock.unix_ns),
                                    "native_receive_monotonic_ns":receive_clock.map(|clock| clock.monotonic_ns.to_string()),
                                    "native_receive_source": if has_recovery_observation { "CURRENT_CODEC_AF_PACKET_FEC_RECOVERY_OBSERVATION" } else { "CURRENT_CODEC_AF_PACKET_RECEIVE" },
                                    "native_receive_slot":slot,
                                }))?;
                            }
                        }
                    }
                }
                true
            }
            Err(_) => {
                stats.wincode_failures += 1;
                false
            }
        };
        if decoded_ok {
            decoded.insert((slot, *index));
        }
        range.clear();
    }
    receipts.flush()?;
    Ok(())
}

fn live_health_json(interface: &str, stats: &LiveStats, elapsed: Duration, receipt_path: &PathBuf) -> serde_json::Value {
    serde_json::json!({"schema":"ALLENHARK_CURRENT_CODEC_LIVE_HEALTH_V1","interface":interface,"elapsed_secs":elapsed.as_secs(),"receipt_path":receipt_path,"packets_seen":stats.packets_seen,"udp20000_packets":stats.udp20000_packets,"unique_payloads":stats.unique_payloads,"parsed_shreds":stats.parsed_shreds,"shred_parse_errors":stats.shred_parse_errors,"data_shreds":stats.data_shreds,"coding_shreds":stats.coding_shreds,"fec_sets_seen":stats.fec_sets_seen,"fec_recovery_attempts":stats.fec_recovery_attempts,"fec_recovery_successes":stats.fec_recovery_successes,"fec_recovery_failures":stats.fec_recovery_failures,"recovered_shreds":stats.recovered_shreds,"complete_ranges":stats.complete_ranges,"deshred_successes":stats.deshred_successes,"deshred_failures":stats.deshred_failures,"wincode_successes":stats.wincode_successes,"wincode_failures":stats.wincode_failures,"decoded_entries":stats.decoded_entries,"decoded_transactions":stats.decoded_transactions,"target_transactions":stats.target_transactions,"target_fee_payer_transactions":stats.target_fee_payer_transactions})
}

fn write_live_health(path: &PathBuf, interface: &str, stats: &LiveStats, elapsed: Duration, receipt_path: &PathBuf) -> Result<()> {
    let mut file = File::create(path)?;
    serde_json::to_writer_pretty(&mut file, &live_health_json(interface, stats, elapsed, receipt_path))?;
    file.write_all(b"\n")?;
    Ok(())
}

fn read_pcap(input: &[u8]) -> Result<(u32, Vec<Packet>)> {
    if input.len() < 24 {
        bail!("pcap header is truncated")
    }
    let magic = &input[..4];
    let little = match magic {
        [0xd4, 0xc3, 0xb2, 0xa1] | [0x4d, 0x3c, 0xb2, 0xa1] => true,
        [0xa1, 0xb2, 0xc3, 0xd4] | [0xa1, 0xb2, 0x3c, 0x4d] => false,
        _ => bail!("unsupported pcap magic {:02x?}", magic),
    };
    let read_u32 = |bytes: &[u8]| -> u32 {
        if little { u32::from_le_bytes(bytes.try_into().unwrap()) } else { u32::from_be_bytes(bytes.try_into().unwrap()) }
    };
    let linktype = read_u32(&input[20..24]);
    let mut offset = 24usize;
    let mut packets = Vec::new();
    while offset + 16 <= input.len() {
        let incl_len = read_u32(&input[offset + 8..offset + 12]) as usize;
        offset += 16;
        if offset + incl_len > input.len() {
            bail!("pcap packet exceeds file boundary")
        }
        packets.push(Packet { payload: input[offset..offset + incl_len].to_vec() });
        offset += incl_len;
    }
    if offset != input.len() {
        bail!("trailing truncated pcap record")
    }
    Ok((linktype, packets))
}

fn extract_udp_payload(packet: &[u8], linktype: u32) -> Result<Option<Vec<u8>>> {
    let ip = match linktype {
        1 => {
            if packet.len() < 14 { bail!("short ethernet frame") }
            let mut offset = 14usize;
            let ethertype = u16::from_be_bytes([packet[12], packet[13]]);
            if ethertype == 0x8100 || ethertype == 0x88a8 {
                if packet.len() < 18 { bail!("short vlan frame") }
                offset = 18;
            }
            &packet[offset..]
        }
        101 => packet,
        _ => bail!("unsupported pcap linktype {linktype}"),
    };
    if ip.len() < 20 || (ip[0] >> 4) != 4 { return Ok(None); }
    let ihl = (ip[0] & 0x0f) as usize * 4;
    if ihl < 20 || ip.len() < ihl + 8 { bail!("short ipv4/udp packet") }
    if ip[9] != 17 { return Ok(None); }
    let udp = &ip[ihl..];
    let destination = u16::from_be_bytes([udp[2], udp[3]]);
    if destination != UDP_PORT { return Ok(None); }
    Ok(Some(udp[8..].to_vec()))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

fn push_failure(report: &mut Report, slot: u64, start_index: u32, end_index: u32, stage: &'static str, error: String, payload: &[u8]) {
    if report.failure_samples.len() >= 20 { return; }
    let prefix_len = payload.len().min(32);
    report.failure_samples.push(FailureSample {
        slot,
        start_index,
        end_index,
        stage,
        error,
        payload_len: payload.len(),
        payload_sha256: sha256_hex(payload),
        prefix_hex: hex::encode(&payload[..prefix_len]),
    });
}
