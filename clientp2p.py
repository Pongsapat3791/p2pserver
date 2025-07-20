# client.py
import socket
import threading
import sys

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

def main():
    """ฟังก์ชันหลักของ Client"""
    if len(sys.argv) != 4:
        print("Usage: python client.py <server_ip> <server_port> <local_port>")
        print("Example: python client.py 203.0.113.10 9000 25565")
        sys.exit(1)

    # --- อ่านค่าจาก Command Line ---
    SERVER_IP = sys.argv[1]
    SERVER_PORT = int(sys.argv[2])
    LOCAL_PORT = int(sys.argv[3])
    LOCAL_HOST = '127.0.0.1' # บริการที่รันบนเครื่องตัวเอง
    # --------------------------------

    try:
        # 1. เชื่อมต่อไปยัง Server กลางเพื่อ "ลงทะเบียน"
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"[*] Connecting to relay server {SERVER_IP}:{SERVER_PORT}...")
        server_conn.connect((SERVER_IP, SERVER_PORT))
        print("[+] Connected to relay server.")

        # 2. รอรับ Public Port จาก Server
        response = server_conn.recv(1024).decode()
        if response.startswith("PUBLIC_PORT:"):
            public_port = response.split(":")[1]
            print("="*40)
            print("  SUCCESS! YOUR SERVICE IS NOW PUBLIC.")
            print(f"  Tell your friends to connect to:")
            print(f"  IP Address: {SERVER_IP}")
            print(f"  Port: {public_port}")
            print("="*40)
        else:
            print(f"[-] Server responded with an error: {response}")
            server_conn.close()
            return

        # 3. เชื่อมต่อไปยัง Service ที่รันในเครื่อง (เช่น Minecraft Server)
        local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print(f"[*] Connecting to local service at {LOCAL_HOST}:{LOCAL_PORT}...")
        local_conn.connect((LOCAL_HOST, LOCAL_PORT))
        print(f"[+] Connected to local service.")

        print("[*] Relay is now active. Forwarding data...")

        # 4. เริ่มกระบวนการส่งต่อข้อมูล (Relay)
        # สร้าง Thread สำหรับส่งข้อมูลจาก Server -> Local Service
        server_to_local_thread = threading.Thread(
            target=forward_data,
            args=(server_conn, local_conn, "Server -> Local")
        )
        # สร้าง Thread สำหรับส่งข้อมูลจาก Local Service -> Server
        local_to_server_thread = threading.Thread(
            target=forward_data,
            args=(local_conn, server_conn, "Local -> Server")
        )

        server_to_local_thread.start()
        local_to_server_thread.start()

        server_to_local_thread.join()
        local_to_server_thread.join()

    except ConnectionRefusedError:
        print(f"[!] Connection to local service at {LOCAL_HOST}:{LOCAL_PORT} was refused.")
        print("[!] Make sure your service (e.g., Minecraft server) is running before starting this script.")
    except Exception as e:
        print(f"[!] An error occurred: {e}")
    finally:
        print("[*] Relay stopped.")
        server_conn.close()
        local_conn.close()

if __name__ == "__main__":
    main()
