# client.py
import socket
import threading
import sys
import time

def forward_data(source_socket, dest_socket, direction):
    """
    ฟังก์ชันสำหรับส่งต่อข้อมูลระหว่าง Socket สองตัว
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                break
            dest_socket.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass # ปล่อยให้ thread จบการทำงานไปเงียบๆ
    finally:
        # ส่งสัญญาณ shutdown เพื่อบอกอีกฝั่งว่าจะไม่มีข้อมูลส่งไปแล้ว
        try:
            dest_socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass

def run_single_session(server_ip, server_port, local_port, local_host):
    """
    ฟังก์ชันสำหรับจัดการการเชื่อมต่อ 1 เซสชันเต็ม (เชื่อมต่อ, relay, จบการทำงาน)
    """
    server_conn = None
    local_conn = None
    try:
        # --- ส่วนที่ 1: เชื่อมต่อไปยัง Server กลาง ---
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"[*] Establishing tunnel to relay server {server_ip}:{server_port}...")
        server_conn.connect((server_ip, server_port))
        
        response = server_conn.recv(1024).decode()
        if not response.startswith("PUBLIC_PORT:"):
            print(f"[-] Server error: {response}")
            return # จบการทำงานของเซสชันนี้
        
        public_port = response.split(":")[1]
        print("="*40)
        print("  SUCCESS! TUNNEL IS ESTABLISHED.")
        print(f"  Your service is available at:")
        print(f"  IP Address: {server_ip}")
        print(f"  Port: {public_port}")
        print("  Waiting for a player to connect...")
        print("="*40)

        # --- ส่วนที่ 2: เชื่อมต่อไปยัง Service ในเครื่อง ---
        # จะรอจนกว่าจะเชื่อมต่อกับ Service ในเครื่องได้สำเร็จ
        while True:
            try:
                local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                local_conn.connect((local_host, local_port))
                print(f"[+] Connected to local service at {local_host}:{local_port}.")
                break # ออกจาก Loop เมื่อเชื่อมต่อสำเร็จ
            except ConnectionRefusedError:
                print(f"[!] Connection to local service ({local_host}:{local_port}) refused. Is it running?")
                print("[*] Retrying in 10 seconds... (Press Ctrl+C to stop this session)")
                time.sleep(10)
        
        # --- ส่วนที่ 3: เริ่ม Relay ข้อมูล ---
        print("[*] Relay is now active. Forwarding data until player disconnects.")
        server_to_local_thread = threading.Thread(target=forward_data, args=(server_conn, local_conn, "Server -> Local"))
        local_to_server_thread = threading.Thread(target=forward_data, args=(local_conn, server_conn, "Local -> Server"))

        server_to_local_thread.start()
        local_to_server_thread.start()

        server_to_local_thread.join()
        local_to_server_thread.join()
        
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        print(f"\n[!] Could not connect to relay server: {e}")
    except KeyboardInterrupt:
        # ดักจับ Ctrl+C ระหว่างรอเชื่อม local service
        raise # ส่งต่อไปให้ loop หลักจัดการ
    except Exception as e:
        print(f"\n[!] An unexpected error occurred in session: {e}")
    finally:
        if server_conn:
            server_conn.close()
        if local_conn:
            local_conn.close()
        print("\n[*] Session ended.")

def main():
    """
    [แก้ไข] ฟังก์ชันหลักที่มี Loop ครอบการทำงานทั้งหมด
    เพื่อให้โปรแกรมทำงานต่อเนื่องและเริ่มเซสชันใหม่เองอัตโนมัติ
    """
    if len(sys.argv) != 4:
        print("Usage: python client.py <server_ip> <server_port> <local_port>")
        print("Example: python client.py 203.0.113.10 9000 25565")
        sys.exit(1)

    SERVER_IP = sys.argv[1]
    SERVER_PORT = int(sys.argv[2])
    LOCAL_PORT = int(sys.argv[3])
    LOCAL_HOST = '127.0.0.1'

    try:
        while True:
            run_single_session(SERVER_IP, SERVER_PORT, LOCAL_PORT, LOCAL_HOST)
            print("="*40)
            print("  READY FOR A NEW SESSION.")
            print("  Starting next session in 5 seconds...")
            print("  (Press Ctrl+C to stop the program)")
            print("="*40)
            time.sleep(5)

    except KeyboardInterrupt:
        print("\n[*] Program stopped by user. Exiting.")
    finally:
        print("[*] Final cleanup complete.")


if __name__ == "__main__":
    main()
