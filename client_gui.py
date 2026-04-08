# client_gui.py
# This is the Tkinter GUI version of the Lynk client.
# It connects to the Lynk server, shows incoming messages,
# and lets the user send text messages and files.

import socket
import threading
import json
import time
import os
import base64
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

# ─── Configuration ────────────────────────────────────────

SERVER_PORT = 9000    # TCP port the server listens on
UDP_PORT    = 55000   # UDP port used for discovery beacons
BUFFER_SIZE = 4096    # How many bytes we read at a time from the socket

# ─── Networking Functions ─────────────────────────────────

def discover_server(timeout=3):
    """
    Listen for a UDP broadcast beacon from the server.
    Returns (ip, port) if found, or (None, None) if nothing heard.
    """
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.settimeout(timeout)
    udp_sock.bind(("", UDP_PORT))

    try:
        data, _ = udp_sock.recvfrom(1024)
        msg = json.loads(data.decode())
        if msg.get("type") == "DISCOVER":
            return msg["payload"]["ip"], msg["payload"]["port"]
    except socket.timeout:
        pass
    finally:
        udp_sock.close()

    return None, None


def send_msg(sock, msg: dict):
    """
    Convert a dict to JSON and send it over TCP.
    We add a newline at the end so the server knows where
    one message ends and the next begins.
    """
    try:
        data = json.dumps(msg) + "\n"
        sock.sendall(data.encode())
    except Exception:
        pass


# ─── Main Application Class ───────────────────────────────

class LynkApp:
    """
    The main Lynk GUI application.
    Builds the window, connects to the server, and handles
    all sending and receiving of messages.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Lynk - Local Network Clipboard & File Sync")
        self.root.geometry("860x580")
        self.root.minsize(640, 400)

        # Session state
        self.tcp_sock     = None
        self.device_id    = None
        self.current_room = "general"

        # StringVar and DoubleVar are Tkinter variables that
        # automatically update any widget linked to them
        self.room_var     = tk.StringVar(value="Room: general")
        self.status_var   = tk.StringVar(value="Connecting...")
        self.progress_var = tk.DoubleVar(value=0)

        self._build_ui()

        # Connect in a background thread so the window doesn't freeze
        threading.Thread(target=self._connect, daemon=True).start()

    # ─── UI Construction ──────────────────────────────────

    def _build_ui(self):
        """Build and arrange all widgets in the window."""

        # Simple light colour scheme — easy to read, nothing fancy
        BG       = "#f0f0f0"   # Light grey — window background
        WHITE    = "#ffffff"   # White — panel backgrounds
        HEADER   = "#3c3f41"   # Dark grey — header bar
        HTEXT    = "#ffffff"   # White — header text
        BORDER   = "#cccccc"   # Light grey — borders
        MUTED    = "#888888"   # Grey — labels and hints
        BTN      = "#4a90d9"   # Blue — buttons
        BTNTEXT  = "#ffffff"   # White — button text
        MONO     = "Courier"   # Monospace font for messages

        self.root.configure(bg=BG)

        # ── Top bar ────────────────────────────────────────
        # Shows the app name, your device ID, and current room

        top_bar = tk.Frame(self.root, bg=HEADER, height=40)
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_bar.grid_propagate(False)

        tk.Label(
            top_bar, text="LYNK",
            font=("Arial", 13, "bold"),
            fg=HTEXT, bg=HEADER
        ).pack(side=tk.LEFT, padx=12)

        # Room label on the right — linked to self.room_var
        # When self.room_var changes, this label updates automatically
        tk.Label(
            top_bar, textvariable=self.room_var,
            font=("Arial", 10),
            fg=HTEXT, bg=HEADER
        ).pack(side=tk.RIGHT, padx=12)

        # Status label shows ID and connection state
        tk.Label(
            top_bar, textvariable=self.status_var,
            font=("Arial", 10),
            fg="#aaaaaa", bg=HEADER
        ).pack(side=tk.RIGHT, padx=12)

        # ── Grid layout ────────────────────────────────────
        # Row 1 (main area) stretches when window is resized
        # Column 0 (left panel) is fixed, column 1 (messages) stretches

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        # ── Left panel — devices and rooms ─────────────────

        left_panel = tk.Frame(self.root, bg=WHITE, width=160, relief=tk.FLAT,
                              highlightbackground=BORDER, highlightthickness=1)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left_panel.grid_propagate(False)

        tk.Label(
            left_panel, text="DEVICES",
            font=("Arial", 8, "bold"),
            fg=MUTED, bg=WHITE, anchor="w"
        ).pack(fill=tk.X, padx=8, pady=(8, 2))

        # Listbox — a scrollable list of text items
        # The server sends us the full device list whenever someone connects/disconnects
        self.device_listbox = tk.Listbox(
            left_panel,
            bg=WHITE, fg="#222222",
            font=(MONO, 10),
            borderwidth=0,
            highlightthickness=0,
            selectbackground="#d0e4f7",
            activestyle="none",
            height=6
        )
        self.device_listbox.pack(fill=tk.X, padx=6)

        # A simple divider line between the two lists
        tk.Frame(left_panel, bg=BORDER, height=1).pack(fill=tk.X, padx=6, pady=8)

        tk.Label(
            left_panel, text="ROOMS",
            font=("Arial", 8, "bold"),
            fg=MUTED, bg=WHITE, anchor="w"
        ).pack(fill=tk.X, padx=8, pady=(0, 2))

        # Room list — double-click to join a room
        self.room_listbox = tk.Listbox(
            left_panel,
            bg=WHITE, fg="#222222",
            font=(MONO, 10),
            borderwidth=0,
            highlightthickness=0,
            selectbackground="#d0e4f7",
            activestyle="none"
        )
        self.room_listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 8))

        # Double-clicking a room name joins it
        self.room_listbox.bind("<Double-Button-1>", self._on_room_click)

        tk.Label(
            left_panel, text="double-click to join",
            font=("Arial", 7), fg=MUTED, bg=WHITE
        ).pack(pady=(0, 6))

        # ── Right panel — message feed ─────────────────────

        right_panel = tk.Frame(self.root, bg=BG)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        # ScrolledText is a Text widget with a built-in scrollbar
        # state=DISABLED means users cannot type in it directly —
        # we control all the content via code
        self.message_area = scrolledtext.ScrolledText(
            right_panel,
            bg=WHITE, fg="#222222",
            font=(MONO, 10),
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT,
            borderwidth=1,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=8, pady=6
        )
        self.message_area.grid(row=0, column=0, sticky="nsew")

        # Text colour tags — each message type gets a different colour
        # These are applied when we call _append_message(text, tag)
        self.message_area.tag_config("system",    foreground="#888888")  # Grey  — system info
        self.message_area.tag_config("sent",      foreground="#1a6b1a")  # Green — messages you sent
        self.message_area.tag_config("broadcast", foreground="#b36200")  # Orange
        self.message_area.tag_config("room",      foreground="#1a50a0")  # Blue
        self.message_area.tag_config("direct",    foreground="#7b1fa2")  # Purple
        self.message_area.tag_config("file",      foreground="#00695c")  # Teal
        self.message_area.tag_config("error",     foreground="#c62828")  # Red

        # ── Bottom bar — input and controls ────────────────

        bottom = tk.Frame(self.root, bg=BG)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        bottom.grid_columnconfigure(0, weight=1)

        # Progress bar — only shown during file transfers
        self.progress_bar = ttk.Progressbar(
            bottom, variable=self.progress_var, maximum=100
        )
        # Not packed yet — we show it only when a file transfer starts

        # Row 0: message input + Send button
        input_row = tk.Frame(bottom, bg=BG)
        input_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        input_row.grid_columnconfigure(0, weight=1)

        self.input_field = tk.Entry(
            input_row,
            font=("Arial", 11),
            relief=tk.FLAT,
            bg=WHITE,
            fg="#222222",
            highlightbackground=BORDER,
            highlightthickness=1
        )
        self.input_field.grid(row=0, column=0, sticky="ew", ipady=5, padx=(0, 6))

        # Pressing Enter sends the message — same as clicking Send
        self.input_field.bind("<Return>", lambda e: self._on_send())

        tk.Button(
            input_row, text="Send",
            bg=BTN, fg=BTNTEXT,
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            padx=16, pady=4,
            cursor="hand2",
            activebackground="#2e6db4",
            activeforeground=BTNTEXT,
            command=self._on_send
        ).grid(row=0, column=1)

        # Row 1: room entry + Join button + Send File button
        control_row = tk.Frame(bottom, bg=BG)
        control_row.grid(row=1, column=0, sticky="ew")

        tk.Label(control_row, text="Room:", bg=BG,
                 font=("Arial", 9), fg=MUTED).pack(side=tk.LEFT)

        self.room_entry = tk.Entry(
            control_row,
            font=("Arial", 10),
            width=14,
            relief=tk.FLAT,
            bg=WHITE,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        self.room_entry.pack(side=tk.LEFT, padx=(4, 4), ipady=3)
        self.room_entry.insert(0, "general")

        tk.Button(
            control_row, text="Join",
            bg=BTN, fg=BTNTEXT,
            font=("Arial", 9), relief=tk.FLAT,
            padx=10, pady=2, cursor="hand2",
            activebackground="#2e6db4",
            activeforeground=BTNTEXT,
            command=self._on_join
        ).pack(side=tk.LEFT, padx=(0, 16))

        tk.Button(
            control_row, text="Send File",
            bg="#555555", fg=BTNTEXT,
            font=("Arial", 9), relief=tk.FLAT,
            padx=10, pady=2, cursor="hand2",
            activebackground="#333333",
            activeforeground=BTNTEXT,
            command=self._on_file_button
        ).pack(side=tk.LEFT)

    # ─── Connection ───────────────────────────────────────

    def _connect(self):
        """
        Runs in a background thread.
        Tries UDP discovery first, then falls back to manual IP entry.
        """
        self._set_status("Searching for server...")
        server_ip, server_port = discover_server(timeout=3)

        if not server_ip:
            server_ip   = self._ask_ip()
            server_port = SERVER_PORT

        if not server_ip:
            self._set_status("Not connected.")
            return

        try:
            self._set_status(f"Connecting to {server_ip}...")
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((server_ip, server_port))
        except Exception as e:
            self._set_status("Connection failed.")
            self._append_message(f"[ERROR] Could not connect: {e}\n", "error")
            return

        # Start receiving messages in the background
        threading.Thread(target=self._receive_loop, daemon=True).start()

        # Wait briefly for the welcome ACK before sending JOIN
        time.sleep(0.5)

        self._join_room("general")

        # Show command help after a short delay so it appears
        # after the welcome messages have already printed
        self.root.after(600, lambda: self._append_message(
            "\n--- Commands ---\n"
            "  /broadcast <msg>       send to every connected device\n"
            "  /direct <id> <msg>     send to one device by ID\n"
            "  /join <room>           join a room\n"
            "  /leave                 leave current room\n"
            "  /help                  show this list again\n"
            "  (just type anything)   sends to your current room\n"
            "Tip: double-click a room in the ROOMS panel to join it\n"
            "----------------\n\n",
            "system"
        ))

    def _ask_ip(self):
        """
        Show a popup asking the user to type a server IP.
        Runs on the main thread via after(), using an Event to wait for it.
        """
        result = [None]
        done   = threading.Event()

        def show():
            from tkinter.simpledialog import askstring
            ip = askstring("Server Not Found", "Enter server IP address:")
            result[0] = ip
            done.set()

        self.root.after(0, show)
        done.wait()
        return result[0]

    # ─── Receive Loop ─────────────────────────────────────

    def _receive_loop(self):
        """
        Runs forever in a background thread.
        Reads data from the server and sends each complete
        message to _display_message on the main thread.
        """
        buffer = ""

        while True:
            try:
                data = self.tcp_sock.recv(BUFFER_SIZE).decode()

                if not data:
                    self._append_message("[!] Disconnected from server.\n", "error")
                    self._set_status("Disconnected.")
                    break

                buffer += data

                # TCP can deliver partial data, so we buffer until
                # we have a complete line (our messages end with \n)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                        # Tkinter is not thread-safe — always update the GUI
                        # from the main thread using root.after()
                        # lambda m=msg captures the current msg value
                        self.root.after(0, lambda m=msg: self._display_message(m))
                    except json.JSONDecodeError:
                        pass

            except Exception:
                break

    def _display_message(self, msg):
        """
        Called on the main thread.
        Displays an incoming message and handles server list updates.
        """
        msg_type = msg.get("type")
        sender   = msg.get("sender", "unknown")
        payload  = msg.get("payload", "")

        if msg_type == "ACK" and sender == "server" and isinstance(payload, dict):
            # Welcome message from server — contains our device ID
            self.device_id = payload.get("device_id", self.device_id)
            self._set_status(f"ID: {self.device_id}")
            self._append_message(f"[connected] your ID is: {self.device_id}\n", "system")

        elif msg_type == "DEVICE_LIST":
            # Server sent the full list of connected devices
            # We clear and rebuild the listbox so it's always accurate
            self.device_listbox.delete(0, tk.END)
            for did in payload:
                self.device_listbox.insert(tk.END, did)

        elif msg_type == "ROOM_LIST":
            # Server sent the full list of known rooms
            # Only add rooms we don't already have listed
            existing = self.room_listbox.get(0, tk.END)
            for room_name in payload:
                if room_name not in existing:
                    self.room_listbox.insert(tk.END, room_name)

        elif msg_type == "BROADCAST":
            self._append_message(f"[broadcast] {sender}: {payload}\n", "broadcast")

        elif msg_type == "ROOM":
            self._append_message(f"[room] {sender}: {payload}\n", "room")

        elif msg_type == "DIRECT":
            self._append_message(f"[direct] {sender} → you: {payload}\n", "direct")

        elif msg_type == "FILE_HEADER":
            filename = payload.get("filename", "received_file")
            size     = payload.get("size", 0)
            data     = payload.get("data")

            self._append_message(
                f"[file] incoming from {sender}: {filename} ({size} bytes)\n", "file"
            )

            if data:
                os.makedirs("files_received", exist_ok=True)
                save_path = os.path.join("files_received", filename)
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(data))
                self._append_message(f"[file] saved to: {save_path}\n", "file")

        elif msg_type == "ERROR":
            self._append_message(f"[error] {payload}\n", "error")

    # ─── Sending ──────────────────────────────────────────

    def _on_send(self):
        """
        Called when the user presses Enter or clicks Send.
        Parses the input and sends the correct message type.
        Also echoes what the user sent into their own message feed.
        """
        if not self.tcp_sock:
            return

        text = self.input_field.get().strip()
        self.input_field.delete(0, tk.END)

        if not text:
            return

        if text.startswith("/broadcast "):
            message = text[11:].strip()
            send_msg(self.tcp_sock, {
                "type": "BROADCAST", "sender": self.device_id,
                "room": None, "target": None, "payload": message
            })
            # Echo the sent message into our own feed so we can see what we sent
            self._append_message(f"[broadcast] you: {message}\n", "sent")

        elif text.startswith("/direct "):
            parts = text[8:].split(" ", 1)
            if len(parts) == 2:
                target_id, message = parts
                send_msg(self.tcp_sock, {
                    "type": "DIRECT", "sender": self.device_id,
                    "room": None, "target": target_id, "payload": message
                })
                self._append_message(f"[direct] you → {target_id}: {message}\n", "sent")
            else:
                self._append_message("[!] usage: /direct <id> <message>\n", "error")

        elif text.startswith("/join "):
            room_name = text[6:].strip()
            self._join_room(room_name)
            self.room_entry.delete(0, tk.END)
            self.room_entry.insert(0, room_name)

        elif text == "/leave":
            self._leave_room()

        elif text == "/help":
            self._append_message(
                "\n--- Commands ---\n"
                "  /broadcast <msg>       send to every connected device\n"
                "  /direct <id> <msg>     send to one device by ID\n"
                "  /join <room>           join a room\n"
                "  /leave                 leave current room\n"
                "  /help                  show this list again\n"
                "  (just type anything)   sends to your current room\n"
                "Tip: double-click a room in the ROOMS panel to join it\n"
                "----------------\n\n",
                "system"
            )

        else:
            # Plain text — send to current room
            send_msg(self.tcp_sock, {
                "type": "ROOM", "sender": self.device_id,
                "room": self.current_room, "target": None, "payload": text
            })
            self._append_message(f"[room] you: {text}\n", "sent")

    def _on_join(self):
        """Called when the user clicks the Join button."""
        room_name = self.room_entry.get().strip()
        if room_name:
            self._join_room(room_name)

    def _on_room_click(self, event):
        """Called when the user double-clicks a room in the ROOMS panel."""
        selection = self.room_listbox.curselection()
        if selection:
            room_name = self.room_listbox.get(selection[0]).strip()
            self._join_room(room_name)
            self.room_entry.delete(0, tk.END)
            self.room_entry.insert(0, room_name)

    def _join_room(self, room_name):
        """Tell the server we are joining a room and update local state."""
        self.current_room = room_name
        self.room_var.set(f"Room: {room_name}")
        if self.tcp_sock:
            send_msg(self.tcp_sock, {
                "type": "JOIN", "sender": self.device_id,
                "room": room_name, "target": None, "payload": None
            })
        self._append_message(f"[joined room: {room_name}]\n", "system")

    def _leave_room(self):
        """Tell the server we are leaving the current room."""
        if self.tcp_sock:
            send_msg(self.tcp_sock, {
                "type": "LEAVE", "sender": self.device_id,
                "room": self.current_room, "target": None, "payload": None
            })
        self._append_message(f"[left room: {self.current_room}]\n", "system")

    # ─── File Sending ─────────────────────────────────────

    def _on_file_button(self):
        """
        Called when the user clicks Send File.
        Opens a file picker and asks for a target device ID.
        """
        if not self.tcp_sock:
            messagebox.showwarning("Not Connected", "You are not connected to a server.")
            return

        filepath = filedialog.askopenfilename(title="Choose a file to send")
        if not filepath:
            return

        from tkinter.simpledialog import askstring
        target_id = askstring("Send File", "Enter the target device ID:")
        if not target_id:
            return

        # Send in a background thread so the GUI stays responsive
        threading.Thread(
            target=self._send_file,
            args=(target_id.strip(), filepath),
            daemon=True
        ).start()

    def _send_file(self, target_id, filepath):
        """
        Runs in a background thread.
        Reads the file, encodes as base64, sends as a single JSON message.
        Updates the progress bar while working.
        """
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        self.root.after(0, lambda: self._append_message(
            f"[file] sending {filename} ({filesize} bytes) → {target_id}...\n", "file"
        ))

        # Show the progress bar
        self.root.after(0, lambda: self.progress_bar.grid(
            row=2, column=0, sticky="ew", pady=(4, 0))
        )

        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        self.root.after(0, lambda: self.progress_var.set(50))  # 50% — file read

        # base64 converts raw binary bytes into plain ASCII text
        # so it can safely travel inside our JSON message
        encoded = base64.b64encode(raw_bytes).decode("utf-8")

        self.root.after(0, lambda: self.progress_var.set(80))  # 80% — encoded

        send_msg(self.tcp_sock, {
            "type":    "FILE_HEADER",
            "sender":  self.device_id,
            "room":    None,
            "target":  target_id,
            "payload": {
                "filename": filename,
                "size":     filesize,
                "data":     encoded
            }
        })

        self.root.after(0, lambda: self.progress_var.set(100))  # 100% — sent

        self.root.after(0, lambda: self._append_message(
            f"[file] sent {filename} to {target_id}\n", "file"
        ))

        # Hide and reset the progress bar after 2 seconds
        self.root.after(2000, lambda: self.progress_bar.grid_remove())
        self.root.after(2000, lambda: self.progress_var.set(0))

    # ─── Helpers ──────────────────────────────────────────

    def _append_message(self, text, tag="system"):
        """
        Add text to the message feed.
        We briefly enable the widget, insert the text, then lock it again.
        The tag sets the text colour (defined in _build_ui).
        """
        self.message_area.config(state=tk.NORMAL)
        self.message_area.insert(tk.END, text, tag)
        self.message_area.see(tk.END)           # Auto-scroll to the latest message
        self.message_area.config(state=tk.DISABLED)

    def _set_status(self, text):
        """Update the status label. Safe to call from any thread."""
        self.root.after(0, lambda: self.status_var.set(text))


# ─── Entry Point ──────────────────────────────────────────

def main():
    root = tk.Tk()
    app  = LynkApp(root)
    root.mainloop()   # Hands control to Tkinter — runs until window is closed

if __name__ == "__main__":
    main()