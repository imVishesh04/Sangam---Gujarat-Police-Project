import sqlite3

con = sqlite3.connect('cctv.db')
cur = con.cursor()

cur.execute('SELECT camera_id, stream_url FROM cameras')
rows = cur.fetchall()

updated = 0
for cam_id, stream_url in rows:
    new_url = None
    if cam_id == 'CAM-AHM-001':
        new_url = 'https://cctv.corp8.cloud/cam06/index.m3u8'
    elif cam_id.startswith('CAM-SENTINEL-'):
        num_str = cam_id.replace('CAM-SENTINEL-', '')
        try:
            num = int(num_str)
            new_url = f'https://cctv.corp8.cloud/cam{num:02d}/index.m3u8'
        except ValueError:
            pass

    if new_url and new_url != stream_url:
        cur.execute('UPDATE cameras SET stream_url=?, format=? WHERE camera_id=?', (new_url, 'hls', cam_id))
        updated += 1
        print(f'Updated {cam_id}: {stream_url} -> {new_url}')

con.commit()
print(f'Total cameras updated in cctv.db: {updated}')
con.close()
