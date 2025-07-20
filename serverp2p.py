# server.py
import socket
import threading
import time

# --- การตั้งค่า ---
# IP ของ Server ที่จะให้ Client มาเชื่อมต่อ (ใช้ 0.0.0.0 เพื่อรับการเชื่อมต่อจากทุก IP)
SERVER_HOST = '0.0.0.0'
# Port ที่จะให้ Client มาเชื่อมต่อเพื่อ "ลงทะเบียน"
SERVER_PORT = 9000
# ช่วง Port ที่จะเปิดให้ผู้เล่นอื่น (Peer) เข้ามาเชื่อมต่อ
# แนะนำให้ใช้ Port ที่สูงกว่า 1024 และตรวจสอบว่า Firewall ของ VPS อนุญาต
PORT_POOL_START = 9001
PORT_POOL_END = 9100
# -----------------

used_ports = set()

def get_free_port():
    """หา Port ที่ว่างใน Pool"""
    for port in range(PORT_POOL_START, PORT_POOL_END + 1):
        if port not in used_ports:
            used_ports.add(port)
            return port
    return None

def release_port(port):
    """คืน Port กลับเข้า Pool เมื่อเลิกใช้งาน"""
    if port in used_ports:
        used_ports.remove(port)

def forward_data(source_socket, dest_socket, direction):
    """ฟังก์ชันสำหรับส่งต่อข้อมูลระหว่าง Socket สองตัว"""
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                print(f"[{direction}] Connection closed. Shutting down.")
                break
            dest_socket.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        print(f"[{direction}] Connection lost.")
    finally:
        source_socket.close()
        dest_socket.close()


def handle_client(client_conn, client_addr):
    """จัดการการเชื่อมต่อจาก Client (ผู้เปิด Host) หนึ่งราย"""
    print(f"[+] Client connected from {client_addr}")
    
    # 1. หา Port ว่างสำหรับให้ Peer เข้ามา
    public_port = get_free_port()
    if public_port is None:
        print("[-] No available ports in the pool. Rejecting client.")
        client_conn.sendall(b"ERROR: No available ports.")
        client_conn.close()
        return

    # 2. สร้าง Socket รอรับการเชื่อมต่อจาก Peer (ผู้เล่น) บน Port ที่ได้มา
    peer_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        peer_listener.bind((SERVER_HOST, public_port))
        peer_listener.listen(1)
        print(f"[*] Assigned public port {public_port} to {client_addr}. Waiting for peer...")
        
        # 3. แจ้ง Port ที่ได้ให้ Client ทราบ
        client_conn.sendall(f"PUBLIC_PORT:{public_port}".encode())

        # 4. รอ Peer มาเชื่อมต่อ (ตั้ง Timeout เผื่อไม่มีใครมา)
        peer_listener.settimeout(300) # รอ 5 นาที
        peer_conn, peer_addr = peer_listener.accept()
        print(f"[+] Peer connected from {peer_addr} on port {public_port}")
        peer_listener.close() # ปิด listener เพราะเราได้ connection แล้ว

        # 5. เริ่มกระบวนการส่งต่อข้อมูล (Relay)
        print(f"[*] Starting relay between {client_addr} and {peer_addr}")
        
        # สร้าง Thread สำหรับส่งข้อมูลจาก Client -> Peer
        client_to_peer_thread = threading.Thread(
            target=forward_data, 
            args=(client_conn, peer_conn, f"{client_addr} -> {peer_addr}")
        )
        # สร้าง Thread สำหรับส่งข้อมูลจาก Peer -> Client
        peer_to_client_thread = threading.Thread(
            target=forward_data, 
            args=(peer_conn, client_conn, f"{peer_addr} -> {client_addr}")
        )

        client_to_peer_thread.start()
        peer_to_client_thread.start()
        
        # รอให้ Thread ทั้งสองทำงานจนจบ (เมื่อมีการปิดการเชื่อมต่อ)
        client_to_peer_thread.join()
        peer_to_client_thread.join()

    except socket.timeout:
        print(f"[-] Peer did not connect for port {public_port}. Closing session.")
    except Exception as e:
        print(f"[!] An error occurred for port {public_port}: {e}")
    finally:
        print(f"[*] Session for port {public_port} ended. Releasing port.")
        release_port(public_port)
        client_conn.close()
        peer_listener.close()


def main():
    """ฟังก์ชันหลักของ Server"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((SERVER_HOST, SERVER_PORT))
    server_socket.listen(5)
    print(f"[*] Server listening on {SERVER_HOST}:{SERVER_PORT}")

    while True:
        try:
            client_conn, client_addr = server_socket.accept()
            # สร้าง Thread ใหม่เพื่อจัดการ Client แต่ละราย จะได้ไม่บล็อกการเชื่อมต่อใหม่
            thread = threading.Thread(target=handle_client, args=(client_conn, client_addr))
            thread.start()
        except KeyboardInterrupt:
            print("\n[!] Server is shutting down.")
            break
        except Exception as e:
            print(f"[!] Server error: {e}")
            time.sleep(1)

    server_socket.close()

if __name__ == "__main__":
    main()
