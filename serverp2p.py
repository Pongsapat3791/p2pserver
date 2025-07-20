# server.py
import socket
import threading
import struct
import time
import itertools

# --- การตั้งค่า ---
SERVER_HOST = '0.0.0.0'
SERVER_CONTROL_PORT = 9000 # Port สำหรับ Client มาขอ Public Port
PORT_POOL_START = 9001
PORT_POOL_END = 9100
# -----------------

used_ports = set()
lock = threading.Lock()

def get_free_port():
    """หา Port ที่ว่างใน Pool แบบ Thread-safe"""
    with lock:
        for port in range(PORT_POOL_START, PORT_POOL_END + 1):
            if port not in used_ports:
                used_ports.add(port)
                return port
        return None

def release_port(port):
    """คืน Port กลับเข้า Pool"""
    with lock:
        if port in used_ports:
            used_ports.remove(port)

def forward_from_peer_to_host(peer_conn, host_conn, player_id, players_lock, players):
    """อ่านข้อมูลจากผู้เล่น (Peer), ใส่ Header, แล้วส่งไปให้ Host"""
    try:
        while True:
            data = peer_conn.recv(4096)
            if not data:
                break
            # สร้าง Header: 4 bytes สำหรับ Player ID, 4 bytes สำหรับความยาวข้อมูล
            header = struct.pack('!II', player_id, len(data))
            host_conn.sendall(header + data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        print(f"[Player {player_id}] Disconnected.")
        with players_lock:
            # [แก้ไข] เพิ่มการตรวจสอบก่อนลบเพื่อป้องกัน KeyError
            if player_id in players:
                del players[player_id]
        # แจ้งให้ Host รู้ว่าผู้เล่นคนนี้หลุดการเชื่อมต่อแล้ว
        try:
            header = struct.pack('!II', player_id, 0) # ส่งข้อมูลความยาว 0
            host_conn.sendall(header)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        peer_conn.close()

def forward_from_host_to_peers(host_conn, players, players_lock):
    """อ่านข้อมูลจาก Host, แกะ Header, แล้วส่งไปให้ผู้เล่น (Peer) ที่ถูกต้อง"""
    try:
        while True:
            # [แก้ไข] อ่าน Header 8 bytes ให้ครบถ้วนเพื่อป้องกัน struct.error
            header_buffer = b''
            while len(header_buffer) < 8:
                packet = host_conn.recv(8 - len(header_buffer))
                if not packet:
                    # การเชื่อมต่อถูกปิดจากฝั่ง Host
                    header_buffer = None
                    break
                header_buffer += packet
            
            if not header_buffer:
                # ออกจาก Loop หลักเมื่อ Host ปิดการเชื่อมต่อ
                break

            player_id, length = struct.unpack('!II', header_buffer)
            
            # อ่านข้อมูลตามความยาวที่ระบุใน Header
            data = b''
            if length > 0:
                while len(data) < length:
                    chunk = host_conn.recv(length - len(data))
                    if not chunk:
                        raise ConnectionError("Host connection lost while reading data payload.")
                    data += chunk

            with players_lock:
                if player_id in players:
                    players[player_id].sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError, ConnectionError) as e:
        print(f"[Host Tunnel] Connection lost: {e}")
    finally:
        # เมื่อ Host หลุด ให้ปิดการเชื่อมต่อของผู้เล่นทั้งหมด
        with players_lock:
            for player_id, peer_conn in players.items():
                peer_conn.close()
            players.clear()
        host_conn.close()

def manage_public_port(public_port):
    """จัดการ Public Port ที่จองไว้ รอรับ Host 1 คน และผู้เล่นหลายๆ คน"""
    print(f"[*] Port Manager for {public_port} is running.")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((SERVER_HOST, public_port))
    listener.listen(10) # รองรับผู้เล่นที่เข้ามาพร้อมกันได้มากขึ้น

    try:
        # 1. รอ Host (Client) มาเชื่อมต่อเพื่อสร้างอุโมงค์
        print(f"[{public_port}] Waiting for Host to establish tunnel...")
        host_conn, host_addr = listener.accept()
        print(f"[{public_port}] Host tunnel established: {host_addr}")

        players = {}
        players_lock = threading.Lock()
        player_id_generator = itertools.count(1) # สร้าง ID ผู้เล่นที่ไม่ซ้ำกัน

        # 2. เริ่ม Thread ที่คอยรับข้อมูลจาก Host แล้วส่งต่อไปยังผู้เล่น
        host_reader_thread = threading.Thread(target=forward_from_host_to_peers, args=(host_conn, players, players_lock))
        host_reader_thread.start()

        # 3. Loop หลักเพื่อรอรับผู้เล่นใหม่ๆ
        while host_reader_thread.is_alive():
            try:
                peer_conn, peer_addr = listener.accept()
                player_id = next(player_id_generator)
                print(f"[{public_port}] Peer connected: {peer_addr}, assigned ID: {player_id}")
                
                with players_lock:
                    players[player_id] = peer_conn
                
                # เริ่ม Thread ที่คอยรับข้อมูลจากผู้เล่นคนนี้ แล้วส่งต่อไปยัง Host
                peer_thread = threading.Thread(target=forward_from_peer_to_host, args=(peer_conn, host_conn, player_id, players_lock, players))
                peer_thread.start()
            except OSError:
                # Listener ถูกปิดแล้ว
                break

        host_reader_thread.join()

    except Exception as e:
        print(f"[!] Critical error in Port Manager {public_port}: {e}")
    finally:
        listener.close()
        release_port(public_port)
        print(f"[*] Port Manager for {public_port} has shut down.")


def main():
    """ฟังก์ชันหลักของ Server ทำหน้าที่เป็นผู้แจก Port"""
    control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    control_socket.bind((SERVER_HOST, SERVER_CONTROL_PORT))
    control_socket.listen(5)
    print(f"[*] Server Control listening on {SERVER_HOST}:{SERVER_CONTROL_PORT}")

    try:
        while True:
            conn, addr = control_socket.accept()
            public_port = get_free_port()
            if public_port:
                print(f"[+] Assigning port {public_port} to {addr}")
                conn.sendall(str(public_port).encode())
                # สร้าง Thread แยกเพื่อจัดการ Port ที่จองไว้นี้โดยเฉพาะ
                manager_thread = threading.Thread(target=manage_public_port, args=(public_port,))
                manager_thread.start()
            else:
                print(f"[-] No available ports for {addr}")
                conn.sendall(b"ERROR:NoPorts")
            conn.close()
    except KeyboardInterrupt:
        print("\n[!] Server is shutting down.")
    finally:
        control_socket.close()

if __name__ == "__main__":
    main()
