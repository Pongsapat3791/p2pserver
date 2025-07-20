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
    """
    [แก้ไข] ฟังก์ชันสำหรับส่งต่อข้อมูลระหว่าง Socket สองตัว
    จัดการการปิดการเชื่อมต่ออย่างนุ่มนวลขึ้น
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                print(f"[{direction}] Connection closed by source. Shutting down destination write.")
                break
            dest_socket.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[{direction}] Connection lost: {e}")
    finally:
        # เมื่อทิศทางหนึ่งจบลง (ไม่ว่าจะด้วย error หรือการปิดปกติ)
        # เราจะส่งสัญญาณ shutdown ไปยังอีกฝั่ง เพื่อบอกว่าจะไม่มีข้อมูลส่งไปแล้ว
        # แต่ไม่ได้ปิด socket ทันที เพื่อให้อีกฝั่งส่งข้อมูลที่ค้างอยู่มาได้
        try:
            dest_socket.shutdown(socket.SHUT_WR)
        except OSError:
            # Socket อาจจะถูกปิดไปแล้วโดย thread อื่น ซึ่งไม่เป็นไร
            pass


def handle_client(client_conn, client_addr):
    """
    [แก้ไข] จัดการการเชื่อมต่อจาก Client และดูแลการปิด Socket ทั้งหมดในตอนท้าย
    """
    print(f"[+] Client connected from {client_addr}")
    
    public_port = get_free_port()
    if public_port is None:
        print("[-] No available ports in the pool. Rejecting client.")
        try:
            client_conn.sendall(b"ERROR: No available ports.")
        except OSError: pass
        client_conn.close()
        return

    peer_listener = None
    peer_conn = None
    try:
        peer_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer_listener.bind((SERVER_HOST, public_port))
        peer_listener.listen(1)
        print(f"[*] Assigned public port {public_port} to {client_addr}. Waiting for peer...")
        
        client_conn.sendall(f"PUBLIC_PORT:{public_port}".encode())

        peer_listener.settimeout(300) # รอ 5 นาที
        peer_conn, peer_addr = peer_listener.accept()
        print(f"[+] Peer connected from {peer_addr} on port {public_port}")
        
        # เมื่อได้ connection จาก peer แล้ว ก็ไม่จำเป็นต้องใช้ listener อีกต่อไป
        peer_listener.close()
        peer_listener = None # ตั้งเป็น None เพื่อไม่ให้ finally พยายามปิดซ้ำ

        print(f"[*] Starting relay between {client_addr} and {peer_addr}")
        
        client_to_peer_thread = threading.Thread(
            target=forward_data, 
            args=(client_conn, peer_conn, f"{client_addr} -> {peer_addr}")
        )
        peer_to_client_thread = threading.Thread(
            target=forward_data, 
            args=(peer_conn, client_conn, f"{peer_addr} -> {client_addr}")
        )

        client_to_peer_thread.start()
        peer_to_client_thread.start()
        
        # รอให้ Thread ทั้งสองทำงานจนจบ
        client_to_peer_thread.join()
        peer_to_client_thread.join()
        print(f"[*] Both forwarding threads for port {public_port} have finished.")

    except socket.timeout:
        print(f"[-] Peer did not connect for port {public_port}. Closing session.")
    except Exception as e:
        print(f"[!] An error occurred for port {public_port}: {e}")
    finally:
        print(f"[*] Session for port {public_port} ended. Cleaning up and releasing port.")
        release_port(public_port)
        
        # ปิด Socket ทั้งหมดที่อาจจะยังเปิดอยู่ เพื่อความปลอดภัย
        if peer_conn:
            peer_conn.close()
        if client_conn:
            client_conn.close()
        if peer_listener: # กรณีเกิด error ก่อนที่จะ close listener
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