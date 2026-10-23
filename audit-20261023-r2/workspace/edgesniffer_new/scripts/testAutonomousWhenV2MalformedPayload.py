import importlib.util, pathlib, tempfile
p=pathlib.Path('/tmp/twoCqAutonomousWhenV2.py')
s=importlib.util.spec_from_file_location('v2test',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
db=m.open_db(pathlib.Path(tempfile.mkdtemp())/'x.sqlite')
m.coverage.canonical_create_mints=lambda tx: []
m.coverage.decode_causal_events=lambda *a: (_ for _ in ()).throw(ValueError('invalid Borsh bool 98'))
m.apply_rows(db,{},[({'signature':'bad','receivedAtUnixNs':1,'slot':1,'rawSha256':'a'},{'successfulAtReceipt':True})],set())
assert db.execute('select count(*) from decoder_rejections').fetchone()[0]==1
m.coverage.decode_causal_events=lambda *a:([],None)
m.apply_rows(db,{},[({'signature':'good','receivedAtUnixNs':2,'slot':2,'rawSha256':'b'},{'successfulAtReceipt':True})],set())
print('MALFORMED_REJECTION_REGRESSION_PASS')
