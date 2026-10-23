import importlib.util
import pathlib
import tempfile


spec = importlib.util.spec_from_file_location('lane', 'twoCqAutonomousWhenV1.py')
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)

with tempfile.TemporaryDirectory() as directory:
    db = lane.open_db(pathlib.Path(directory) / 'lane.sqlite3')
    for slot in (10, 11, 12, 13, 14):
        db.execute('INSERT INTO slot_queue(slot,receipt_ns,finality) VALUES(?,?,?)', (slot, slot, 'PENDING'))
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (f'sig{slot}:0', 'mint', slot, slot, 0, 0, 1, 'u', 'c', 1, 1, '{}', '{}', 'raw', 'PENDING'))
        db.execute('INSERT INTO creates VALUES(?,?,?,?,?,?)', (f'mint{slot}', f'sig{slot}', slot, slot, 'raw', 'PENDING'))
        db.execute('INSERT INTO teacher_labels VALUES(?,?,?,?,?,?,?,?,?)', (f'sig{slot}:mint', f'sig{slot}', f'mint{slot}', slot, slot, 0, 0, 'raw', 'PENDING'))
    db.commit()

    calls = []

    def fake_rpc(_url, method, params):
        assert method == 'getSignatureStatuses'
        calls.append(params[0])
        return {'value': [{'slot': int(signature[3:]), 'err': None, 'confirmationStatus': 'finalized'} for signature in params[0]]}

    lane.rpc = fake_rpc
    lane.FINALIZE_BATCH_SIZE = 64
    lane.FINALIZE_MAX_SIGNATURES = 256
    assert lane.finalize(db, 'fake', 100) == 5
    assert db.execute("SELECT count(*) FROM slot_queue WHERE finality='FINAL'").fetchone()[0] == 5
    assert db.execute("SELECT count(*) FROM events WHERE finality='FINAL'").fetchone()[0] == 5
    assert db.execute("SELECT count(*) FROM teacher_labels WHERE finality='FINAL'").fetchone()[0] == 5
    assert len(calls) == 1
    assert lane.finalize(db, 'fake', 100) == 0

print('signature-status finalizer ordering/idempotency fixture: PASS')
