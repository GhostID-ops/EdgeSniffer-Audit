import importlib.util
spec = importlib.util.spec_from_file_location('f', '/usr/local/lib/8ycb-canonical/finalizer.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
x = m.canonical_economic_artifacts('/var/lib/8ycb-canonical')
print(len(x), list(x)[:10])
print(x.get('mint:GvZh7H3Qd2XAoDNcRM7TxEojGbtkGXohYbtnmx4YktTq', {}).get('status'))
