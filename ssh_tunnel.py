"""Reverse SSH tunnel: forwards VDS:8088 -> localhost:8088"""
import sys
import time
import socket
import select
import threading
import paramiko

VDS_HOST = "80.87.203.161"
VDS_USER = "root"
VDS_PASS = "Dohod100mln$"
REMOTE_PORT = 8088       # port on VDS (nginx proxy_pass target)
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8088        # local dashboard

def handler(chan):
    sock = socket.socket()
    try:
        sock.connect((LOCAL_HOST, LOCAL_PORT))
    except Exception as e:
        print(f"[tunnel] forward connect failed: {e}")
        chan.close()
        return
    while True:
        r, _, _ = select.select([sock, chan], [], [], 60)
        if sock in r:
            data = sock.recv(32768)
            if not data:
                break
            chan.sendall(data)
        if chan in r:
            data = chan.recv(32768)
            if not data:
                break
            sock.sendall(data)
    chan.close()
    sock.close()

def reverse_tunnel(transport):
    transport.request_port_forward("127.0.0.1", REMOTE_PORT)
    print(f"[tunnel] Reverse tunnel active: VDS:{REMOTE_PORT} -> localhost:{LOCAL_PORT}")
    print(f"[tunnel] Dashboard URL: https://newtekpro.ru/ozon/")
    while True:
        chan = transport.accept(60)
        if chan is None:
            continue
        t = threading.Thread(target=handler, args=(chan,), daemon=True)
        t.start()

def main():
    while True:
        try:
            print(f"[tunnel] Connecting to {VDS_HOST}...")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(VDS_HOST, username=VDS_USER, password=VDS_PASS, timeout=10)
            print(f"[tunnel] Connected!")
            reverse_tunnel(client.get_transport())
        except KeyboardInterrupt:
            print("[tunnel] Stopped by user")
            sys.exit(0)
        except Exception as e:
            print(f"[tunnel] Error: {e}, reconnecting in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    main()
