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
HEALTH_CHECK_INTERVAL = 60 # วินาที: ความถี่ในการตรวจสอบ Port ที่ค้าง
# -----------------

# --- Global State ---
used_ports = set()
active_managers = {} # [ใหม่] Dict สำหรับเก็บ Thread ที่จัดการแต่ละ Port: {port: thread_object}
lock = threading.Lock()
# --------------------

def get_free_port():
    """หา Port ที่ว่างใน Pool แบบ Thread-safe"""
    with lock:
        for port in range(PORT_POOL_START, PORT_POOL_END + 1):
            if port not in used_ports:
                used_ports.add(port)
                return port
        return None

def release_port(port):
    """
    [แก้ไข] คืน Port กลับเข้า Pool และล้างข้อมูล Thread ที่เกี่ยวข้อง
    ฟังก์ชันนี้จะถูกเรียกเมื่อ session จบลงปกติ หรือโดย Health Checker
    """
    with lock:
        # ตรวจสอบก่อนลบเพื่อป้องกัน Error หากมีการเรียกซ้ำ
        if port in used_ports:
            used_ports.remove(port)
            print(f"[*] Port {port} released and returned to the pool.")
        if port in active_managers:
            del active_managers[port]

# [ใหม่] ฟังก์ชันสำหรับตรวจสอบและเก็บกวาด Port ที่ไม่ถูกใช้งาน
def port_health_checker():
    """
    ตรวจสอบสถานะของ Port Manager Threads เป็นระยะๆ
    และเรียกคืน Port จาก Thread ที่หยุดทำงานไปแล้ว (เช่น เกิดข้อผิดพลาด)
    """
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL)
        print(f"[Health Check] Running check for inactive ports... (Currently used: {len(used_ports)})")

        reclaim_ports = []
        with lock:
            # สร้าง list จาก .items() เพื่อให้สามารถแก้ไข dict ได้อย่างปลอดภัย
            for port, thread in list(active_managers.items()):
                if not thread.is_alive():
                    reclaim_ports.append(port)

        if reclaim_ports:
            print(f"[Health Check] Found dead threads for ports: {reclaim_ports}. Reclaiming...")
            for port in reclaim_ports:
                # release_port จะจัดการ lock ของตัวเอง
                release_port(port)
        else:
            print("[Health Check] All active ports seem healthy.")


def forward_from_peer_to_host(peer_conn, host_conn, player_id, players_lock, players):
    """อ่านข้อมูลจากผู้เล่น (Peer), ใส่ Header, แล้วส่งไปให้ Host"""
    try:
        while True:
            data = peer_conn.recv(4096)
            if not data:
                break
            header = struct.pack('!II', player_id, len(data))
            host_conn.sendall(header + data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        print(f"[Player {player_id}] Disconnected.")
        with players_lock:
            if player_id in players:
                del players[player_id]
        try:
            # แจ้งให้ Host รู้ว่าผู้เล่นคนนี้หลุดการเชื่อมต่อแล้ว (ส่งข้อมูลความยาว 0)
            header = struct.pack('!II', player_id, 0)
            host_conn.sendall(header)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        peer_conn.close()

def forward_from_host_to_peers(host_conn, players, players_lock):
    """อ่านข้อมูลจาก Host, แกะ Header, แล้วส่งไปให้ผู้เล่น (Peer) ที่ถูกต้อง"""
    try:
        while True:
            header_buffer = b''
            while len(header_buffer) < 8:
                packet = host_conn.recv(8 - len(header_buffer))
                if not packet:
                    header_buffer = None
                    break
                header_buffer += packet
            
            if not header_buffer:
                break

            player_id, length = struct.unpack('!II', header_buffer)
            
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
    
    try:
        listener.bind((SERVER_HOST, public_port))
    except OSError as e:
        print(f"[!] Critical error: Could not bind to port {public_port}. {e}")
        print(f"[!] This port might be in use by another process. Releasing it.")
        release_port(public_port) # พยายาม release port ถ้า bind ไม่ได้
        return

    listener.listen(10)

    try:
        print(f"[{public_port}] Waiting for Host to establish tunnel...")
        # [แก้ไข] เพิ่ม timeout เพื่อไม่ให้ listener.accept() ค้างตลอดไปหากมีปัญหา
        listener.settimeout(300) # 5 นาทีสำหรับรอ Host
        host_conn, host_addr = listener.accept()
        print(f"[{public_port}] Host tunnel established: {host_addr}")
        listener.settimeout(None) # ปิด timeout เมื่อเชื่อมต่อสำเร็จ

        players = {}
        players_lock = threading.Lock()
        player_id_generator = itertools.count(1)

        host_reader_thread = threading.Thread(target=forward_from_host_to_peers, args=(host_conn, players, players_lock))
        host_reader_thread.start()

        while host_reader_thread.is_alive():
            try:
                # [แก้ไข] ตั้ง timeout สำหรับการรอผู้เล่นใหม่ เพื่อให้ loop ไม่ block ตลอดไป
                # และทำให้ thread สามารถจบการทำงานได้ถ้า host หลุดไปแล้ว
                listener.settimeout(1.0)
                peer_conn, peer_addr = listener.accept()
                listener.settimeout(None)
                
                player_id = next(player_id_generator)
                print(f"[{public_port}] Peer connected: {peer_addr}, assigned ID: {player_id}")
                
                with players_lock:
                    players[player_id] = peer_conn
                
                peer_thread = threading.Thread(target=forward_from_peer_to_host, args=(peer_conn, host_conn, player_id, players_lock, players))
                peer_thread.start()

            except socket.timeout:
                # ไม่เป็นไร แค่ไม่มีใครเชื่อมต่อเข้ามาใน 1 วินาที
                # loop จะวนกลับไปเช็คว่า host_reader_thread ยังทำงานอยู่หรือไม่
                continue
            except OSError:
                # Listener ถูกปิดแล้ว
                break

        host_reader_thread.join()

    except socket.timeout:
        print(f"[{public_port}] Timed out waiting for Host connection. Shutting down this port manager.")
    except Exception as e:
        print(f"[!] Critical error in Port Manager {public_port}: {e}")
    finally:
        listener.close()
        release_port(public_port) # <--- จุดสำคัญ: คืน Port เมื่อจบการทำงาน
        print(f"[*] Port Manager for {public_port} has shut down.")


def main():
    """ฟังก์ชันหลักของ Server ทำหน้าที่เป็นผู้แจก Port และเริ่ม Health Checker"""
    # [ใหม่] เริ่ม Thread สำหรับ Health Checker
    health_thread = threading.Thread(target=port_health_checker, daemon=True)
    health_thread.start()
    print("[+] Port health checker service started.")

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
                
                manager_thread = threading.Thread(target=manage_public_port, args=(public_port,))
                
                # [ใหม่] บันทึก Thread ที่สร้างขึ้นเพื่อการตรวจสอบ
                with lock:
                    active_managers[public_port] = manager_thread
                
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
