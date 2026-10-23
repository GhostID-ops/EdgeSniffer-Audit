import sqlite3

p = '/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1/state-plane/full-meta-canonical-state.sqlite3'
con = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
print('tables')
for name, sql in con.execute("select name,sql from sqlite_master where type='table' order by name"):
    print(name, sql[:1600].replace('\n', ' '))
print('indexes')
for row in con.execute("select name,tbl_name,sql from sqlite_master where type='index'"):
    print(row)
