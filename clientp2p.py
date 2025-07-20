# client.py
import socket
import threading
import sys
import time

def forward_data(source_socket, dest_socket, direction):
    """
    [แก้ไข] ฟังก์ชันสำหรับส่งต่อข้อมูลระหว่าง Socket สองตัว
    จัดการการปิดการเชื่อมต่ออย่างนุ่มนวลขึ้น
    """
    try:
        while True:
            data = source_socket.recv(4096)
            if not data:
                # นำข้อความ "Shutting down destination write" ออกตามที่ต้องการ
                print(f"[{direction}] Connection closed by source.")
                break
            dest_socket.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"[{direction}] Connection lost: {e}")
    finally:
        # เมื่อทิศทางหนึ่งจบลง เราจะส่งสัญญาณ shutdown ไปยังอีกฝั่ง
        try:
            dest_socket.shutdown(socket.SHUT_WR)
        except OSError:
            pass

def main():
    """
    [แก้ไข] ฟังก์ชันหลักของ Client
    เพิ่ม Loop เพื่อให้โปรแกรมทำงานต่อเนื่อง และรอรับการเชื่อมต่อใหม่หลังจากเซสชันเก่าจบลง
    """
    if len(sys.argv) != 4:
        print("Usage: python client.py <server_ip> <server_port> <local_port>")
        print("Example: python client.py 203.0.113.10 9000 25565")
        sys.exit(1)

    SERVER_IP = sys.argv[1]
    SERVER_PORT = int(sys.argv[2])
    LOCAL_PORT = int(sys.argv[3])
    LOCAL_HOST = '127.0.0.1'
    
    # เพิ่ม Loop หลักเพื่อให้โปรแกรมทำงานตลอดเวลาจนกว่าผู้ใช้จะกด Ctrl+C
    try:
        while True:
            server_conn = None
            local_conn = None
            
            try:
                # --- ส่วนที่ 1: เชื่อมต่อไปยัง Server กลาง ---
                server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                print(f"[*] Connecting to relay server {SERVER_IP}:{SERVER_PORT}...")
                server_conn.connect((SERVER_IP, SERVER_PORT))
                print("[+] Connected to relay server.")

                # --- ส่วนที่ 2: รอรับ Public Port จาก Server ---
                response = server_conn.recv(1024).decode()
                if response.startswith("PUBLIC_PORT:"):
                    public_port = response.split(":")[1]
                    print("="*40)
                    print("  SUCCESS! YOUR SERVICE IS NOW PUBLIC.")
                    print(f"  Tell your friends to connect to:")
                    print(f"  IP Address: {SERVER_IP}")
                    print(f"  Port: {public_port}")
                    print("  Waiting for a player to connect...")
                    print("="*40)
                else:
                    print(f"[-] Server responded with an error: {response}")
                    print("[*] Retrying in 10 seconds...")
                    time.sleep(10)
                    continue # กลับไปเริ่ม Loop ใหม่

                # --- ส่วนที่ 3: เชื่อมต่อไปยัง Service ในเครื่อง (เช่น Minecraft) ---
                try:
                    local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    print(f"[*] Connecting to local service at {LOCAL_HOST}:{LOCAL_PORT}...")
                    local_conn.connect((LOCAL_HOST, LOCAL_PORT))
                    print(f"[+] Connected to local service.")
                except ConnectionRefusedError:
                    print(f"[!] Connection to local service at {LOCAL_HOST}:{LOCAL_PORT} was refused.")
                    print("[!] Make sure your service (e.g., Minecraft server) is running.")
                    # ปิดการเชื่อมต่อกับ server กลางก่อนที่จะลองใหม่
                    if server_conn:
                        server_conn.close()
                    print("[*] Retrying in 10 seconds...")
                    time.sleep(10)
                    continue # กลับไปเริ่ม Loop ใหม่

                # --- ส่วนที่ 4: เริ่มกระบวนการส่งต่อข้อมูล (Relay) ---
                print("[*] Relay is now active. Forwarding data...")
                server_to_local_thread = threading.Thread(
                    target=forward_data,
                    args=(server_conn, local_conn, "Server -> Local")
                )
                local_to_server_thread = threading.Thread(
                    target=forward_data,
                    args=(local_conn, server_conn, "Local -> Server")
                )

                server_to_local_thread.start()
                local_to_server_thread.start()

                server_to_local_thread.join()
                local_to_server_thread.join()
                print("[*] Both forwarding threads have finished.")

            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                print(f"[!] Connection to relay server failed: {e}")
                print("[*] Retrying in 10 seconds...")
                time.sleep(10)
                # ไม่ต้องทำอะไรเพิ่ม เพราะจะวน Loop ใหม่เอง
            except Exception as e:
                print(f"[!] An unexpected error occurred: {e}")
                print("[*] Retrying in 10 seconds...")
                time.sleep(10)
            finally:
                # ปิดการเชื่อมต่อของเซสชันนี้ให้เรียบร้อยก่อนเริ่มใหม่
                if server_conn:
                    server_conn.close()
                if local_conn:
                    local_conn.close()
                
                print("\n" + "="*40)
                print("  SESSION ENDED. READY FOR A NEW CONNECTION.")
                print("  (Press Ctrl+C to stop the program)")
                print("="*40 + "\n")
                time.sleep(2) # หน่วงเวลาเล็กน้อยก่อนเริ่ม Loop ใหม่

    except KeyboardInterrupt:
        print("\n[*] Program stopped by user. Exiting.")
    finally:
        print("[*] Final cleanup complete.")


if __name__ == "__main__":
    main()
