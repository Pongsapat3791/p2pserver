import tkinter as tk
from tkinter import messagebox, scrolledtext
import socket
import threading
import struct
import sys
import queue

class ClientLogicThread(threading.Thread):
    """
    This class runs the core client logic in a separate thread to prevent the GUI from freezing.
    It uses queues to communicate status, results, and errors back to the main GUI thread.
    """
    def __init__(self, server_ip, control_port, local_port, status_queue):
        super().__init__()
        self.server_ip = server_ip
        self.control_port = control_port
        self.local_port = local_port
        self.local_host = '127.0.0.1'
        self.status_queue = status_queue
        
        self.server_conn = None
        self.shutdown_event = threading.Event()
        self.local_connections = {}
        self.local_lock = threading.Lock()

    def stop(self):
        """Signals the thread to shut down gracefully."""
        self.shutdown_event.set()
        if self.server_conn:
            try:
                # Closing the socket will raise an exception in the listening thread, causing it to exit.
                self.server_conn.close()
            except OSError:
                pass
        
        with self.local_lock:
            for conn in self.local_connections.values():
                try:
                    conn.close()
                except OSError:
                    pass

    def _put_status(self, message_type, data):
        """Puts a message into the queue for the GUI to process."""
        self.status_queue.put({'type': message_type, 'data': data})

    def run(self):
        """The main logic of the client thread."""
        try:
            # 1. Request Public Port
            self._put_status('status', f"Requesting port from {self.server_ip}:{self.control_port}...")
            public_port = self._request_public_port()
            if not public_port:
                # Error is already sent by the request function
                return

            self._put_status('success', {'ip': self.server_ip, 'port': public_port})

            # 2. Establish persistent tunnel
            self._put_status('status', f"Connecting to tunnel at {self.server_ip}:{public_port}...")
            self.server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_conn.connect((self.server_ip, public_port))
            self._put_status('status', "Tunnel established. Status: Running")

            # 3. Start forwarding data
            self._forward_from_server_to_local()

        except Exception as e:
            if not self.shutdown_event.is_set():
                self._put_status('error', f"A critical error occurred: {e}")
        finally:
            self._put_status('stopped', "Connection closed.")

    def _request_public_port(self):
        """Requests a public port from the server's Server port."""
        try:
            req_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            req_socket.connect((self.server_ip, self.control_port))
            response = req_socket.recv(1024).decode()
            req_socket.close()
            if response.startswith("ERROR"):
                self._put_status('error', f"Server error: {response}")
                return None
            return int(response)
        except Exception as e:
            self._put_status('error', f"Failed to request port: {e}")
            return None

    def _forward_from_local_to_server(self, local_conn, player_id):
        """Reads from a local connection and forwards data to the server."""
        try:
            while not self.shutdown_event.is_set():
                data = local_conn.recv(4096)
                if not data:
                    break
                header = struct.pack('!II', player_id, len(data))
                if self.server_conn:
                    self.server_conn.sendall(header + data)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass # Socket was likely closed by another thread.
        finally:
             # Send a disconnection signal for this player
            if not self.shutdown_event.is_set() and self.server_conn:
                try:
                    header = struct.pack('!II', player_id, 0)
                    self.server_conn.sendall(header)
                except OSError:
                    pass

    def _forward_from_server_to_local(self):
        """Reads from the server tunnel and forwards data to the correct local connection."""
        try:
            while not self.shutdown_event.is_set():
                header_buffer = b''
                while len(header_buffer) < 8:
                    if self.shutdown_event.is_set() or not self.server_conn: return
                    packet = self.server_conn.recv(8 - len(header_buffer))
                    if not packet:
                        header_buffer = None
                        break
                    header_buffer += packet
                
                if not header_buffer:
                    self._put_status('status', "Server closed the connection.")
                    break

                player_id, length = struct.unpack('!II', header_buffer)
                
                data = b''
                if length > 0:
                    while len(data) < length:
                        chunk = self.server_conn.recv(length - len(data))
                        if not chunk: raise ConnectionError("Tunnel connection lost.")
                        data += chunk

                with self.local_lock:
                    if self.shutdown_event.is_set(): break
                    
                    if player_id not in self.local_connections and length > 0:
                        try:
                            local_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            local_conn.connect((self.local_host, self.local_port))
                            self.local_connections[player_id] = local_conn
                            
                            upstream_thread = threading.Thread(target=self._forward_from_local_to_server, args=(local_conn, player_id))
                            upstream_thread.daemon = True
                            upstream_thread.start()
                        except ConnectionRefusedError:
                            self._put_status('status', f"[Warning] Connection to local service for Player {player_id} refused.")
                            continue
                    
                    if length == 0:
                        if player_id in self.local_connections:
                            self.local_connections[player_id].close()
                            del self.local_connections[player_id]
                        continue

                    if player_id in self.local_connections:
                        try:
                            self.local_connections[player_id].sendall(data)
                        except OSError:
                            pass # Socket may have been closed.
        finally:
            with self.local_lock:
                for conn in self.local_connections.values():
                    conn.close()


class P2PClientGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("P2P Client")
        self.root.geometry("400x200")
        self.root.resizable(False, False)

        self.client_thread = None
        self.status_queue = queue.Queue()

        # --- UI Elements ---
        self.ip_var = tk.StringVar(value="127.0.0.1")
        self.control_port_var = tk.StringVar(value="9000")
        self.local_port_var = tk.StringVar(value="25565")
        
        self.public_ip_var = tk.StringVar(value="N/A")
        self.public_port_var = tk.StringVar(value="N/A")
        self.status_var = tk.StringVar(value="Status: Idle")

        main_frame = tk.Frame(root, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top Frame for inputs
        top_frame = tk.Frame(main_frame)
        top_frame.pack(fill=tk.X)

        # Middle Frame for status
        middle_frame = tk.Frame(main_frame)
        middle_frame.pack(fill=tk.X, pady=10)

        # Bottom Frame for buttons
        bottom_frame = tk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # --- Inputs ---
        tk.Label(top_frame, text="Server IP:").grid(row=0, column=0, sticky="w")
        self.ip_entry = tk.Entry(top_frame, textvariable=self.ip_var)
        self.ip_entry.grid(row=0, column=1, sticky="ew")

        tk.Label(top_frame, text="Server Port:").grid(row=1, column=0, sticky="w")
        self.control_port_entry = tk.Entry(top_frame, textvariable=self.control_port_var)
        self.control_port_entry.grid(row=1, column=1, sticky="ew")

        tk.Label(top_frame, text="Local Port:").grid(row=2, column=0, sticky="w")
        self.local_port_entry = tk.Entry(top_frame, textvariable=self.local_port_var)
        self.local_port_entry.grid(row=2, column=1, sticky="ew")
        
        top_frame.columnconfigure(1, weight=1)

        # --- Status Display ---
        tk.Label(middle_frame, text="Public IP:").grid(row=0, column=0, sticky="w")
        tk.Label(middle_frame, textvariable=self.public_ip_var).grid(row=0, column=1, sticky="w")
        
        tk.Label(middle_frame, text="Public Port:").grid(row=1, column=0, sticky="w")
        tk.Label(middle_frame, textvariable=self.public_port_var).grid(row=1, column=1, sticky="w")

        self.status_label = tk.Label(middle_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10,0))
        
        # --- Buttons ---
        self.start_button = tk.Button(bottom_frame, text="Start", command=self.start_client)
        self.start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.stop_button = tk.Button(bottom_frame, text="Stop", command=self.stop_client, state=tk.DISABLED)
        self.stop_button.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(5, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_queue()

    def start_client(self):
        server_ip = self.ip_var.get()
        try:
            control_port = int(self.control_port_var.get())
            local_port = int(self.local_port_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Ports must be numbers.")
            return

        self.set_ui_state(is_running=True)
        self.status_var.set("Status: Connecting...")
        self.public_ip_var.set("N/A")
        self.public_port_var.set("N/A")
        
        self.client_thread = ClientLogicThread(server_ip, control_port, local_port, self.status_queue)
        self.client_thread.start()

    def stop_client(self):
        if self.client_thread and self.client_thread.is_alive():
            self.status_var.set("Status: Stopping...")
            self.client_thread.stop()
            # The thread will put a 'stopped' message in the queue upon exit.
            # The UI will reset once that message is processed.

    def set_ui_state(self, is_running):
        """Enable/disable UI elements based on client state."""
        state = tk.DISABLED if is_running else tk.NORMAL
        self.start_button.config(state=state)
        self.ip_entry.config(state=state)
        self.control_port_entry.config(state=state)
        self.local_port_entry.config(state=state)
        
        stop_state = tk.NORMAL if is_running else tk.DISABLED
        self.stop_button.config(state=stop_state)

    def process_queue(self):
        """Process messages from the client thread to update the GUI."""
        try:
            while True:
                message = self.status_queue.get_nowait()
                msg_type = message.get('type')
                data = message.get('data')

                if msg_type == 'status':
                    self.status_var.set(f"Status: {data}")
                elif msg_type == 'error':
                    self.status_var.set(f"Status: Error")
                    messagebox.showerror("Client Error", data)
                    self.set_ui_state(is_running=False)
                elif msg_type == 'success':
                    self.public_ip_var.set(data['ip'])
                    self.public_port_var.set(data['port'])
                elif msg_type == 'stopped':
                    self.status_var.set("Status: Stopped")
                    self.public_ip_var.set("N/A")
                    self.public_port_var.set("N/A")
                    self.set_ui_state(is_running=False)
                    self.client_thread = None

        except queue.Empty:
            pass # No new messages
        finally:
            self.root.after(100, self.process_queue) # Check again in 100ms

    def on_closing(self):
        """Handle window close event."""
        if self.client_thread and self.client_thread.is_alive():
            self.stop_client()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = P2PClientGUI(root)
    root.mainloop()