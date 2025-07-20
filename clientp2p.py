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

def main():
    """
    [แก้ไข] ฟังก์ชันหลักของ Client
    เชื่อมต่อกับ Server แค่ครั้งเดียว และวน Loop เพื่อสร้างการเชื่อมต่อย่อยไปยัง Local Service
    """
    if len(sys.argv) != 4:
        print("Usage: python client.py <server_ip> <server_port> <local_port>")
        print("Example: python client.py 203.0.113.10 9000 25565")
        sys.exit(1)

    SERVER_IP = sys.argv[1]
    SERVER_PORT = int(sys.argv[2])
    LOCAL_PORT = int(sys.argv[3])
    LOCAL_HOST = '127.0.0.1'
    
    server_conn = None
    try:
        # --- ส่วนที่ 1: เชื่อมต่อไปยัง Server กลางแค่ครั้งเดียว ---
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"[*] Establishing persistent tunnel to relay server {SERVER_IP}:{SERVER_PORT}...")
        server_conn.connect((SERVER_IP, SERVER_PORT))
        
        response = server_conn.recv(1024).decode()
        if not response.startswith("PUBLIC_PORT:"):
            print(f"[-] Server error: {response}")
            return
        
        public_port = response.split(":")[1]
        print("="*40)
        print("  SUCCESS! TUNNEL IS ESTABLISHED.")
        print(f"  Your service is permanently available at:")
        print(f"  IP Address: {SERVER_IP}")
        print(f"  Port: {public_port}")
        print("  (Press Ctrl+C to stop)")
        print("="*40)

        # --- ส่วนที่ 2: Loop เพื่อจัดการผู้เล่นแต่ละคน ---
        while True:
            local_conn = None
            try:
                # สำหรับผู้เล่นใหม่แต่ละคน ให้สร้างการเชื่อมต่อใหม่ไปยัง Local Service
                print(f"[*] Preparing local connection for the next player session...")
                local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                local_conn.connect((LOCAL_HOST, LOCAL_PORT))
                print(f"[+] Local service connected. Ready to relay data.")
                
                # เริ่มต้น Relay สำหรับเซสชันนี้
                # หมายเหตุ: เรากำลังใช้ server_conn เดิมซ้ำๆ ในแต่ละ Loop
                server_to_local_thread = threading.Thread(target=forward_data, args=(server_conn, local_conn, "Server -> Local"))
                local_to_server_thread = threading.Thread(target=forward_data, args=(local_conn, server_conn, "Local -> Server"))

                server_to_local_thread.start()
                local_to_server_thread.start()

                server_to_local_thread.join()
                local_to_server_thread.join()
                
            except ConnectionRefusedError:
                print(f"[!] Connection to local service at {LOCAL_HOST}:{LOCAL_PORT} was refused.")
                print("[!] Make sure your service (e.g., Minecraft server) is running.")
                print("[*] Will retry in 10 seconds...")
                time.sleep(10)
            finally:
                if local_conn:
                    local_conn.close()
                print("\n[*] Player session ended. Ready for the next one.\n")

    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        print(f"\n[!] Lost connection to relay server: {e}")
    except KeyboardInterrupt:
        print("\n[*] Program stopped by user. Exiting.")
    except Exception as e:
        print(f"\n[!] An unexpected error occurred: {e}")
    finally:
        if server_conn:
            server_conn.close()
        print("[*] Tunnel closed. Final cleanup complete.")

if __name__ == "__main__":
    main()