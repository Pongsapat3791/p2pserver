# server.py
import socket
import threading
import time

# --- การตั้งค่า ---
SERVER_HOST = '0.0.0.0'
SERVER_PORT = 9000
PORT_POOL_START = 9001
PORT_POOL_END = 9100
# -----------------

used_ports = set()

def get_free_port():
    for port in range(PORT_POOL_START, PORT_POOL_END + 1):
        if port not in used_ports:
            used_ports.add(port)
            return port
    return None

def release_port(port):
    if port in used_ports:
        used_ports.remove(port)

def forward_data(source_socket, dest_socket, direction):
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                break
            dest_socket.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass # ปล่อยให้ thread จบการทำงานไปเงียบๆ
    finally:
        try:
            dest_socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass

def handle_client(client_conn, client_addr):
    """
    [แก้ไข] จัดการ Client 1 ราย แต่รองรับ Peer (ผู้เล่น) ได้หลายคนตามลำดับ
    """
    print(f"[+] Client connected: {client_addr}")
    public_port = get_free_port()
    if public_port is None:
        try:
            client_conn.sendall(b"ERROR: No available ports.")
        except OSError: pass
        client_conn.close()
        return

    peer_listener = None
    try:
        # 1. สร้าง Listener สำหรับรอรับผู้เล่น (Peer) แค่ครั้งเดียว
        peer_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer_listener.bind((SERVER_HOST, public_port))
        peer_listener.listen(5)
        print(f"[*] Assigned public port {public_port} to {client_addr}. Listener is active.")
        client_conn.sendall(f"PUBLIC_PORT:{public_port}".encode())

        # 2. Loop เพื่อรอรับผู้เล่นหลายๆ คน nตามลำดับ
        while True:
            peer_conn = None
            peer_addr = 'unknown'
            try:
                print(f"[*] Port {public_port}: Waiting for a peer to connect...")
                # บรรทัดนี้จะหยุดรอจนกว่าจะมีผู้เล่นใหม่เชื่อมต่อเข้ามา
                peer_conn, peer_addr = peer_listener.accept()
                print(f"[+] Peer connected from {peer_addr} on port {public_port}")

                # 3. เริ่มต้น Relay สำหรับเซสชันของผู้เล่นคนนี้
                print(f"[*] Starting relay between client and peer {peer_addr}")
                client_to_peer_thread = threading.Thread(target=forward_data, args=(client_conn, peer_conn, "C->P"))
                peer_to_client_thread = threading.Thread(target=forward_data, args=(peer_conn, client_conn, "P->C"))
                
                client_to_peer_thread.start()
                peer_to_client_thread.start()

                client_to_peer_thread.join()
                peer_to_client_thread.join()

            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                 print(f"[-] Connection error in peer loop: {e}. Assuming client disconnected.")
                 break # ออกจาก Loop เพื่อจบการทำงาน
            finally:
                if peer_conn:
                    peer_conn.close()
                print(f"[*] Peer session with {peer_addr} ended. Ready for next peer.")
                # วนกลับไปรอ accept() ใหม่

    except Exception as e:
        print(f"[!] An error occurred for client {client_addr}: {e}")
    finally:
        print(f"[*] Main session for client {client_addr} on port {public_port} is ending. Cleaning up.")
        release_port(public_port)
        if peer_listener:
            peer_listener.close()
        if client_conn:
            client_conn.close()

def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((SERVER_HOST, SERVER_PORT))
    server_socket.listen(5)
    print(f"[*] Server listening on {SERVER_HOST}:{SERVER_PORT}")
    while True:
        try:
            client_conn, client_addr = server_socket.accept()
            thread = threading.Thread(target=handle_client, args=(client_conn, client_addr))
            thread.start()
        except KeyboardInterrupt:
            print("\n[!] Server is shutting down.")
            break
        except Exception as e:
            print(f"[!] Server error: {e}")
    server_socket.close()

if __name__ == "__main__":
    main()