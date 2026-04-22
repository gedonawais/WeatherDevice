from ftplib import FTP, error_perm
resp1 = ""
try:
    ftp = FTP('ftp.metops.net')
    ftp.login('gedonsoft', 'loHtWAkvpDjEC47RzmhjC')
    ftp.set_pasv(True)
    ftp.cwd('upload')

    with open('weather.py', 'rb') as f:
        resp1 = ftp.storbinary('STOR weather.py', f)

    # FTP returns a text message — '226 Transfer complete' means success
    if resp1.startswith('226'):
        print("Upload successful!")
    else:
        print(f"Unexpected FTP response: {resp1}")

except error_perm as e:
    print(f"Permission or FTP error: {e}")
except Exception as e:
    print(f"Upload failed: {e}")
finally:
    try:
        ftp.quit()
    except:
        pass
