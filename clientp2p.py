# client.py
import socket
import threading
import struct
import sys
import time

def forward_from_local_to_server(local_conn, server_conn, player_id):
    """อ่านข้อมูลจาก Local Service, ใส่ Header, แล้วส่งไปให้ Server"""
    try:
        while True:
            data = local_conn.recv(4096)
            if not data:
                break
            header = struct.pack('!II', player_id, len(data))
            server_conn.sendall(header + data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        # เมื่อ Socket ถูกปิดโดย Thread อื่น, Thread นี้จะจบการทำงานไปเงียบๆ
        pass
    # [แก้ไข] นำ local_conn.close() ออกไป เพราะ Thread หลักจะเป็นผู้จัดการ

def forward_from_server_to_local(server_conn, local_target_addr):
    """
    [หัวใจหลัก] อ่านข้อมูลจาก Server, แกะ Header,
    แล้วสร้าง/จัดการการเชื่อมต่อย่อยไปยัง Local Service
    """
    local_connections = {}
    local_lock = threading.Lock()

    try:
        while True:
            # อ่าน Header 8 bytes ให้ครบถ้วน
            header_buffer = b''
            while len(header_buffer) < 8:
                packet = server_conn.recv(8 - len(header_buffer))
                if not packet:
                    header_buffer = None
                    break
                header_buffer += packet
            
            if not header_buffer:
                print("[Tunnel] Server closed the connection.")
                break
            
            player_id, length = struct.unpack('!II', header_buffer)
            
            # อ่านข้อมูลตามความยาวที่ระบุ
            data = b''
            if length > 0:
                while len(data) < length:
                    chunk = server_conn.recv(length - len(data))
                    if not chunk:
                        raise ConnectionError("Tunnel connection lost while reading data payload.")
                    data += chunk

            with local_lock:
                # กรณีผู้เล่นใหม่
                if player_id not in local_connections:
                    if length == 0:
                        continue
                    
                    print(f"[Player {player_id}] New connection detected. Connecting to local service...")
                    try:
                        local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        local_conn.connect(local_target_addr)
                        local_connections[player_id] = local_conn
                        
                        upstream_thread = threading.Thread(target=forward_from_local_to_server, args=(local_conn, server_conn, player_id))
                        upstream_thread.start()
                        print(f"[Player {player_id}] Local connection established.")
                    except ConnectionRefusedError:
                        print(f"[!] Could not connect to local service for Player {player_id}.")
                        continue

                # ถ้า length เป็น 0 หมายถึงผู้เล่นคนนี้หลุดการเชื่อมต่อ
                if length == 0:
                    if player_id in local_connections:
                        print(f"[Player {player_id}] Disconnection signal received. Closing local connection.")
                        local_connections[player_id].close() # Thread นี้เป็นผู้ปิดเท่านั้น
                        del local_connections[player_id]
                    continue

                # ส่งข้อมูลไปยัง Local Service ที่ถูกต้อง
                if player_id in local_connections:
                    try:
                        local_connections[player_id].sendall(data)
                    except OSError:
                        # Socket อาจถูกปิดไปแล้ว
                        pass

    except (ConnectionResetError, BrokenPipeError, OSError, ConnectionError) as e:
        print(f"[Tunnel] Connection error: {e}")
    finally:
        print("[Tunnel] Shutting down all local connections.")
        with local_lock:
            for conn in local_connections.values():
                conn.close()
        server_conn.close()


def request_public_port(server_ip, server_control_port):
    """เชื่อมต่อไปยัง Server เพื่อขอ Public Port แค่ครั้งเดียว"""
    try:
        print(f"[*] Requesting a public port from {server_ip}:{server_control_port}...")
        req_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        req_socket.connect((server_ip, server_control_port))
        response = req_socket.recv(1024).decode()
        req_socket.close()
        if response.startswith("ERROR"):
            print(f"[-] Server could not assign a port: {response}")
            return None
        return int(response)
    except Exception as e:
        print(f"[!] Failed to request port: {e}")
        return None

def main():
    """ฟังก์ชันหลัก ทำหน้าที่ขอ Port, สร้างอุโมงค์, แล้วเริ่มระบบจัดการผู้เล่น"""
    if len(sys.argv) != 4:
        print("Usage: python client.py <server_ip> <control_port> <local_port>")
        print("Example: python client.py 203.0.113.10 9000 25565")
        sys.exit(1)

    SERVER_IP = sys.argv[1]
    SERVER_CONTROL_PORT = int(sys.argv[2])
    LOCAL_PORT = int(sys.argv[3])
    LOCAL_HOST = '127.0.0.1'

    # 1. ขอ Public Port มาแค่ครั้งเดียว
    public_port = request_public_port(SERVER_IP, SERVER_CONTROL_PORT)
    if not public_port:
        print("[!] Could not get a public port. Exiting.")
        return

    print("="*40)
    print("  SUCCESS! YOUR PERMANENT PORT IS ASSIGNED.")
    print(f"  Your service is available at:")
    print(f"  IP Address: {SERVER_IP}")
    print(f"  Port: {public_port}")
    print("="*40)
    
    try:
        # 2. สร้างอุโมงค์ถาวรไปยัง Public Port
        print(f"[*] Establishing persistent tunnel to {SERVER_IP}:{public_port}...")
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_conn.connect((SERVER_IP, public_port))
        print("[+] Tunnel established. Ready to accept multiple players.")
        
        # 3. เริ่ม Thread หลักที่คอยจัดการข้อมูลจากอุโมงค์
        main_thread = threading.Thread(target=forward_from_server_to_local, args=(server_conn, (LOCAL_HOST, LOCAL_PORT)))
        main_thread.start()
        main_thread.join() # รอจนกว่าอุโมงค์จะถูกปิด

    except KeyboardInterrupt:
        print("\n[*] Program stopped by user.")
    except Exception as e:
        print(f"\n[!] A critical error occurred: {e}")
    finally:
        print("[*] Final cleanup complete.")

if __name__ == "__main__":
    main()
