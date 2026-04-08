# client_gui.py
# This is the Tkinter GUI version of the Lynk client.
# It does everything client.py does — discover the server,
# connect via TCP, send and receive messages and files —
# but instead of a plain terminal, it shows a proper window
# with a device list, room list, message feed, and file transfer button.

import socket        # For TCP connection to server and UDP discovery
import threading     # For running the receive loop in the background
import json          # For encoding/decoding messages
import time          # For a small startup delay
import os            # For checking file paths and sizes
import base64        # For encoding file bytes into text so they fit in JSON
import tkinter as tk                        # The main GUI library
from tkinter import ttk, scrolledtext, filedialog, messagebox
# ttk          = themed widgets (nicer looking buttons, progress bar)
# scrolledtext = a text box that automatically gets a scrollbar
# filedialog   = the "Open File" popup window
# messagebox   = small popup alerts (like "File not found")

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
    udp_sock.settimeout(timeout)   # Give up after this many seconds
    udp_sock.bind(("", UDP_PORT))  # Listen on the discovery port

    try:
        data, _ = udp_sock.recvfrom(1024)       # Wait for a beacon packet
        msg = json.loads(data.decode())          # Decode the JSON beacon

        if msg.get("type") == "DISCOVER":
            ip   = msg["payload"]["ip"]
            port = msg["payload"]["port"]
            return ip, port

    except socket.timeout:
        pass   # No beacon arrived in time — return None below

    finally:
        udp_sock.close()   # Always close the socket when done

    return None, None


def send_msg(sock, msg: dict):
    """
    Serialize a Python dict to JSON and send it over TCP.
    We append '\n' so the server knows where this message ends.
    """
    try:
        data = json.dumps(msg) + "\n"   # Convert dict → JSON string → add newline
        sock.sendall(data.encode())      # Send all bytes (handles partial sends)
    except Exception:
        pass   # Silently ignore — the connection may have dropped


# ─── Main Application Class ───────────────────────────────

class LynkApp:
    """
    This class holds the entire Lynk GUI application.
    It builds the window, connects to the server, and handles
    all sending and receiving of messages.

    We use a class so that all the widgets, the socket, and the
    session state (device_id, room) live together in one place
    instead of scattered across global variables.
    """

    def __init__(self, root):
        """
        __init__ runs once when we create a LynkApp object.
        'root' is the main Tkinter window passed in from main().
        """

        self.root = root                  # Save the window reference
        self.root.title("Lynk")           # Set the window title bar text
        self.root.geometry("900x600")     # Set starting window size (width x height)
        self.root.configure(bg="#1a1a2e") # Set background colour (dark navy)
        self.root.minsize(700, 450)       # Prevent the window from getting too small

        # ── Session state ──────────────────────────────────
        self.tcp_sock     = None              # The TCP socket (set after connecting)
        self.device_id    = None              # Our unique ID (assigned by the server)
        self.current_room = "general"         # The room we're currently in

        self.room_var     = tk.StringVar(value="Room: general")  # Shown in top bar
        self.status_var   = tk.StringVar(value="Connecting...")  # Status label text
        self.progress_var = tk.DoubleVar(value=0)                # Progress bar (0–100)

        # ── Build the UI ───────────────────────────────────
        self._build_ui()

        # ── Connect to the server in a background thread ───
        # We use a thread so the window doesn't freeze while
        # waiting for the UDP beacon to arrive
        threading.Thread(target=self._connect, daemon=True).start()

    # ─── UI Construction ──────────────────────────────────

    def _build_ui(self):
        """
        Create and arrange all the widgets in the window.
        Tkinter uses a grid system — rows and columns like a table.
        """

        # ── Colour palette ─────────────────────────────────
        BG        = "#1a1a2e"   # Dark navy — main background
        PANEL     = "#16213e"   # Slightly lighter navy — panel backgrounds
        ACCENT    = "#0f3460"   # Blue — header and borders
        TEXT      = "#e0e0e0"   # Light grey — regular text
        MUTED     = "#a0a0a0"   # Dimmer grey — labels and hints
        HIGHLIGHT = "#e94560"   # Red-pink — accent colour for the title
        ENTRY_BG  = "#0d2137"   # Very dark — input field background

        # ── Top bar ────────────────────────────────────────
        # A thin bar across the top showing the app name,
        # connection status, and current room name.

        top_bar = tk.Frame(self.root, bg=ACCENT, height=45)
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_bar.grid_propagate(False)   # Keep height fixed at 45px

        # App title on the left
        tk.Label(
            top_bar, text="  LYNK",
            font=("Courier", 16, "bold"),
            fg=HIGHLIGHT, bg=ACCENT
        ).pack(side=tk.LEFT, padx=10)

        # Room name on the right — updates automatically when room_var changes
        tk.Label(
            top_bar, textvariable=self.room_var,
            font=("Arial", 11),
            fg=TEXT, bg=ACCENT
        ).pack(side=tk.RIGHT, padx=15)

        # Status label (shows "Connecting...", "ID: abc123", etc.)
        tk.Label(
            top_bar, textvariable=self.status_var,
            font=("Arial", 10),
            fg=MUTED, bg=ACCENT
        ).pack(side=tk.RIGHT, padx=20)

        # ── Grid resize behaviour ──────────────────────────
        # weight=1 means this row/column stretches when the window resizes
        self.root.grid_rowconfigure(1, weight=1)     # Main area stretches vertically
        self.root.grid_columnconfigure(0, weight=0)  # Left panel — fixed width
        self.root.grid_columnconfigure(1, weight=1)  # Right panel — stretches

        # ── Left panel — devices + rooms ───────────────────

        left_panel = tk.Frame(self.root, bg=PANEL, width=180)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        left_panel.grid_propagate(False)   # Keep width fixed at 180px

        # DEVICES label
        tk.Label(
            left_panel, text="DEVICES",
            font=("Arial", 9, "bold"),
            fg=MUTED, bg=PANEL
        ).pack(anchor="w", padx=10, pady=(10, 4))

        # Listbox showing all connected device IDs
        self.device_listbox = tk.Listbox(
            left_panel,
            bg=PANEL, fg=TEXT,
            selectbackground=ACCENT,   # Highlight colour when an item is clicked
            font=("Courier", 10),
            borderwidth=0,
            highlightthickness=0,      # Remove the blue focus border
            activestyle="none",        # No underline on selected item
            height=6                   # Fixed height — 6 lines tall
        )
        self.device_listbox.pack(fill=tk.X, padx=6)

        # ROOMS label
        tk.Label(
            left_panel, text="ROOMS",
            font=("Arial", 9, "bold"),
            fg=MUTED, bg=PANEL
        ).pack(anchor="w", padx=10, pady=(14, 4))

        # Listbox showing all known rooms
        # Double-clicking a room joins it automatically
        self.room_listbox = tk.Listbox(
            left_panel,
            bg=PANEL, fg=TEXT,
            selectbackground=ACCENT,
            font=("Courier", 10),
            borderwidth=0,
            highlightthickness=0,
            activestyle="none"
        )
        self.room_listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 8))

        # Bind double-click on the rooms listbox to _on_room_click
        self.room_listbox.bind("<Double-Button-1>", self._on_room_click)

        # ── Right panel — message feed ─────────────────────

        right_panel = tk.Frame(self.root, bg=BG)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        # ScrolledText = a Text widget with a built-in scrollbar
        # state=DISABLED means users cannot type in it directly
        self.message_area = scrolledtext.ScrolledText(
            right_panel,
            bg=PANEL, fg=TEXT,
            font=("Courier", 10),
            state=tk.DISABLED,     # Read-only — we insert text via code only
            wrap=tk.WORD,          # Wrap long lines at word boundaries
            borderwidth=0,
            highlightthickness=0,
            padx=10, pady=8
        )
        self.message_area.grid(row=0, column=0, sticky="nsew")

        # Colour tags — applied when we insert text to colour it differently
        self.message_area.tag_config("system",    foreground="#a0a0a0")  # Grey
        self.message_area.tag_config("broadcast", foreground="#f0a500")  # Orange
        self.message_area.tag_config("room",      foreground="#4fc3f7")  # Light blue
        self.message_area.tag_config("direct",    foreground="#81c784")  # Green
        self.message_area.tag_config("file",      foreground="#ce93d8")  # Purple
        self.message_area.tag_config("error",     foreground="#e94560")  # Red

        # ── Bottom bar — input + controls ──────────────────

        bottom_bar = tk.Frame(self.root, bg=ACCENT, height=95)
        bottom_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom_bar.grid_propagate(False)
        bottom_bar.grid_columnconfigure(0, weight=1)

        # Progress bar — shown during file transfers, hidden otherwise
        self.progress_bar = ttk.Progressbar(
            bottom_bar,
            variable=self.progress_var,   # Linked to self.progress_var (0–100)
            maximum=100
        )
        # Not shown yet — we call .grid() on it only when a transfer starts

        # Row 0: message input field + Send button
        input_frame = tk.Frame(bottom_bar, bg=ACCENT)
        input_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        input_frame.grid_columnconfigure(0, weight=1)

        self.input_field = tk.Entry(
            input_frame,
            bg=ENTRY_BG, fg=TEXT,
            insertbackground=TEXT,   # Cursor colour inside the entry
            font=("Arial", 11),
            relief=tk.FLAT,
            borderwidth=0
        )
        self.input_field.grid(row=0, column=0, sticky="ew", ipady=6, padx=(0, 8))

        # Pressing Enter triggers _on_send — same as clicking Send
        self.input_field.bind("<Return>", lambda e: self._on_send())

        tk.Button(
            input_frame, text="Send",
            bg="#0f3460", fg=TEXT,
            activebackground="#e94560",
            activeforeground="white",
            font=("Arial", 10, "bold"),
            relief=tk.FLAT,
            padx=14, pady=4,
            cursor="hand2",
            command=self._on_send
        ).grid(row=0, column=1)

        # Row 1: room controls + file button
        control_frame = tk.Frame(bottom_bar, bg=ACCENT)
        control_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        tk.Label(
            control_frame, text="Room:",
            fg=MUTED, bg=ACCENT,
            font=("Arial", 9)
        ).pack(side=tk.LEFT)

        # Text entry where the user types a room name to join
        self.room_entry = tk.Entry(
            control_frame,
            bg=ENTRY_BG, fg=TEXT,
            insertbackground=TEXT,
            font=("Arial", 10),
            width=14,
            relief=tk.FLAT
        )
        self.room_entry.pack(side=tk.LEFT, padx=(4, 4), ipady=4)
        self.room_entry.insert(0, "general")   # Pre-fill with default room name

        tk.Button(
            control_frame, text="Join",
            bg="#0f3460", fg=TEXT,
            activebackground="#e94560",
            font=("Arial", 9), relief=tk.FLAT,
            padx=10, pady=3, cursor="hand2",
            command=self._on_join
        ).pack(side=tk.LEFT, padx=(0, 16))

        # File button — opens a file picker dialog
        tk.Button(
            control_frame, text="📎  Send File",
            bg="#0f3460", fg=TEXT,
            activebackground="#e94560",
            font=("Arial", 9), relief=tk.FLAT,
            padx=10, pady=3, cursor="hand2",
            command=self._on_file_button
        ).pack(side=tk.LEFT)

    # ─── Connection ───────────────────────────────────────

    def _connect(self):
        """
        Runs in a background thread.
        Tries UDP discovery first, then falls back to a manual IP dialog.
        Opens the TCP connection and starts the receive loop.
        """

        self._set_status("Searching for server...")
        server_ip, server_port = discover_server(timeout=3)

        if not server_ip:
            # Discovery failed — ask the user to type the server IP manually
            server_ip   = self._ask_ip()
            server_port = SERVER_PORT

        if not server_ip:
            # User cancelled the dialog — stop here
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

        # Start the receive loop in a background thread
        threading.Thread(target=self._receive_loop, daemon=True).start()

        # Wait briefly for the welcome ACK before sending JOIN
        time.sleep(0.5)

        # Join the default room
        self._join_room("general")

        # Show the help text after a short delay so it appears after the
        # welcome messages have already printed
        self.root.after(600, lambda: self._append_message(
            "\nCommands you can type in the input box:\n"
            "  /broadcast <msg>      → Send to every connected device\n"
            "  /direct <id> <msg>    → Send to one specific device\n"
            "  /join <room>          → Join a room by name\n"
            "  /leave                → Leave current room\n"
            "  /help                 → Show this list again\n"
            "  Just type anything    → Sends to your current room\n\n"
            "Tip: Double-click a room in the ROOMS panel to join it.\n\n",
            "system"
        ))

    def _ask_ip(self):
        """
        Show a popup asking the user to enter a server IP.
        Must run on the main thread — we use threading.Event to wait for it.
        """
        result = [None]            # List so the inner function can write to it
        done   = threading.Event() # Flag we wait on until the dialog closes

        def show():
            from tkinter.simpledialog import askstring
            ip = askstring("Server Not Found", "Enter server IP address:")
            result[0] = ip
            done.set()   # Signal that we have a result

        self.root.after(0, show)   # Schedule show() on the main thread
        done.wait()                # Block this background thread until done
        return result[0]

    # ─── Receive Loop ─────────────────────────────────────

    def _receive_loop(self):
        """
        Runs forever in a background thread.
        Reads incoming data from the server and passes each
        complete message to _display_message on the main thread.
        """
        buffer = ""

        while True:
            try:
                data = self.tcp_sock.recv(BUFFER_SIZE).decode()

                if not data:
                    # Empty data means the server closed the connection
                    self._append_message("[!] Disconnected from server.\n", "error")
                    self._set_status("Disconnected.")
                    break

                buffer += data

                # Process every complete line (one JSON message per line)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)

                    if not line.strip():
                        continue

                    try:
                        msg = json.loads(line)
                        # Schedule display on the main thread (Tkinter is not thread-safe)
                        # lambda m=msg captures the current value of msg in the loop
                        self.root.after(0, lambda m=msg: self._display_message(m))
                    except json.JSONDecodeError:
                        pass

            except Exception:
                break

    def _display_message(self, msg):
        """
        Called on the main thread to display an incoming message.
        Also updates the device list whenever we see a new sender.
        """
        msg_type = msg.get("type")
        sender   = msg.get("sender", "unknown")
        payload  = msg.get("payload", "")

        # Any message from a real device (not the server) → add to device list
        if sender and sender != "server":
            self._add_device(sender)

        if msg_type == "ACK" and sender == "server" and isinstance(payload, dict):
            # Welcome message — server tells us our assigned device ID
            self.device_id = payload.get("device_id", self.device_id)
            self._set_status(f"ID: {self.device_id}")
            self._append_message(f"[Lynk] Connected! Your ID: {self.device_id}\n", "system")
            # Add ourselves to the device list
            self._add_device(self.device_id)

        elif msg_type == "BROADCAST":
            self._append_message(f"[BROADCAST] {sender}: {payload}\n", "broadcast")

        elif msg_type == "ROOM":
            self._append_message(f"[ROOM] {sender}: {payload}\n", "room")

        elif msg_type == "DIRECT":
            self._append_message(f"[DIRECT] {sender}: {payload}\n", "direct")

        elif msg_type == "FILE_HEADER":
            filename = payload.get("filename", "received_file")
            size     = payload.get("size", 0)
            data     = payload.get("data")

            self._append_message(
                f"[FILE] Incoming from {sender}: {filename} ({size} bytes)\n", "file"
            )

            if data:
                os.makedirs("files_received", exist_ok=True)
                save_path = os.path.join("files_received", filename)
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(data))   # Decode base64 → raw bytes → save
                self._append_message(f"[FILE] Saved to: {save_path}\n", "file")

        elif msg_type == "ERROR":
            self._append_message(f"[ERROR] {payload}\n", "error")

    # ─── Sending ──────────────────────────────────────────

    def _on_send(self):
        """
        Called when the user presses Enter or clicks Send.
        Reads the input field and sends the appropriate message type.
        """
        if not self.tcp_sock:
            return

        text = self.input_field.get().strip()   # Read the input
        self.input_field.delete(0, tk.END)      # Clear the input field

        if not text:
            return

        if text.startswith("/broadcast "):
            message = text[11:].strip()
            send_msg(self.tcp_sock, {
                "type": "BROADCAST", "sender": self.device_id,
                "room": None, "target": None, "payload": message
            })

        elif text.startswith("/direct "):
            # Split only on the first space to get device_id and message separately
            parts = text[8:].split(" ", 1)
            if len(parts) == 2:
                send_msg(self.tcp_sock, {
                    "type": "DIRECT", "sender": self.device_id,
                    "room": None, "target": parts[0], "payload": parts[1]
                })
            else:
                self._append_message("[!] Usage: /direct <id> <message>\n", "error")

        elif text.startswith("/join "):
            room_name = text[6:].strip()
            self._join_room(room_name)
            # Also update the room entry box to match
            self.room_entry.delete(0, tk.END)
            self.room_entry.insert(0, room_name)

        elif text == "/leave":
            self._leave_room()

        elif text == "/help":
            self._append_message(
                "\nCommands:\n"
                "  /broadcast <msg>      → Send to every connected device\n"
                "  /direct <id> <msg>    → Send to one specific device\n"
                "  /join <room>          → Join a room by name\n"
                "  /leave                → Leave current room\n"
                "  /help                 → Show this list again\n"
                "  Just type anything    → Sends to your current room\n\n"
                "Tip: Double-click a room in the ROOMS panel to join it.\n\n",
                "system"
            )

        else:
            # No command prefix — send as a regular room message
            send_msg(self.tcp_sock, {
                "type": "ROOM", "sender": self.device_id,
                "room": self.current_room, "target": None, "payload": text
            })

    def _on_join(self):
        """Called when the user clicks the Join button."""
        room_name = self.room_entry.get().strip()
        if room_name:
            self._join_room(room_name)

    def _on_room_click(self, event):
        """
        Called when the user double-clicks a room in the ROOMS panel.
        Joins that room and updates the room entry box.
        """
        selection = self.room_listbox.curselection()   # Get the index of the clicked item
        if selection:
            room_name = self.room_listbox.get(selection[0]).strip()
            self._join_room(room_name)
            # Sync the room entry box to show the joined room
            self.room_entry.delete(0, tk.END)
            self.room_entry.insert(0, room_name)

    def _join_room(self, room_name):
        """Tell the server we want to join a room and update local state."""
        self.current_room = room_name
        self.room_var.set(f"Room: {room_name}")   # Update top bar label
        if self.tcp_sock:
            send_msg(self.tcp_sock, {
                "type": "JOIN", "sender": self.device_id,
                "room": room_name, "target": None, "payload": None
            })
        self._append_message(f"[Lynk] Joined room: {room_name}\n", "system")
        # Add to the ROOMS panel
        self.root.after(0, lambda: self._add_room(room_name))

    def _leave_room(self):
        """Tell the server we are leaving the current room."""
        if self.tcp_sock:
            send_msg(self.tcp_sock, {
                "type": "LEAVE", "sender": self.device_id,
                "room": self.current_room, "target": None, "payload": None
            })
        self._append_message(f"[Lynk] Left room: {self.current_room}\n", "system")

    # ─── File Sending ─────────────────────────────────────

    def _on_file_button(self):
        """
        Called when the user clicks the Send File button.
        Opens a file picker, asks for a target device ID,
        then sends the file in a background thread.
        """
        if not self.tcp_sock:
            messagebox.showwarning("Not Connected", "You are not connected to a server.")
            return

        # Open the OS file picker dialog — returns chosen path or "" if cancelled
        filepath = filedialog.askopenfilename(title="Choose a file to send")

        if not filepath:
            return

        # Ask which device should receive the file
        from tkinter.simpledialog import askstring
        target_id = askstring("Send File", "Enter the target device ID:")

        if not target_id:
            return

        # Run the file send in a background thread so the GUI stays responsive
        threading.Thread(
            target=self._send_file,
            args=(target_id.strip(), filepath),
            daemon=True
        ).start()

    def _send_file(self, target_id, filepath):
        """
        Runs in a background thread.
        Reads the file, encodes it as base64, sends in one JSON message.
        Updates the progress bar while working.
        """
        filename = os.path.basename(filepath)    # Just the filename, not the full path
        filesize = os.path.getsize(filepath)     # File size in bytes

        self.root.after(0, lambda: self._append_message(
            f"[FILE] Sending {filename} ({filesize} bytes) → {target_id}...\n", "file"
        ))

        # Show the progress bar at the top of the bottom bar
        self.root.after(0, lambda: self.progress_bar.grid(
            row=0, column=0, sticky="ew", padx=10, pady=(4, 0)
        ))

        # Read the file as raw bytes
        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        self.root.after(0, lambda: self.progress_var.set(50))   # 50% — file read done

        # Encode raw bytes as base64 text so they fit safely inside JSON
        # base64 turns binary data into plain ASCII characters
        encoded = base64.b64encode(raw_bytes).decode("utf-8")

        self.root.after(0, lambda: self.progress_var.set(80))   # 80% — encoding done

        # Send everything in one FILE_HEADER message
        send_msg(self.tcp_sock, {
            "type":    "FILE_HEADER",
            "sender":  self.device_id,
            "room":    None,
            "target":  target_id,
            "payload": {
                "filename": filename,
                "size":     filesize,
                "data":     encoded    # The base64 encoded file content
            }
        })

        self.root.after(0, lambda: self.progress_var.set(100))  # 100% — sent

        self.root.after(0, lambda: self._append_message(
            f"[FILE] Sent {filename} to {target_id}\n", "file"
        ))

        # Hide the progress bar and reset it after 2 seconds
        self.root.after(2000, lambda: self.progress_bar.grid_remove())
        self.root.after(2000, lambda: self.progress_var.set(0))

    # ─── GUI Helpers ──────────────────────────────────────

    def _append_message(self, text, tag="system"):
        """
        Add a line of text to the message feed.
        We briefly enable the widget, insert text, then disable it again.
        The tag controls the text colour (defined in _build_ui).
        """
        self.message_area.config(state=tk.NORMAL)       # Temporarily allow edits
        self.message_area.insert(tk.END, text, tag)     # Add text at the bottom
        self.message_area.see(tk.END)                   # Auto-scroll to the new line
        self.message_area.config(state=tk.DISABLED)     # Lock it again

    def _set_status(self, text):
        """Update the status label in the top bar. Safe to call from any thread."""
        self.root.after(0, lambda: self.status_var.set(text))

    def _add_device(self, device_id):
        """Add a device ID to the DEVICES panel if not already listed."""
        existing = self.device_listbox.get(0, tk.END)   # Get all current entries
        if device_id not in existing:
            self.device_listbox.insert(tk.END, device_id)

    def _remove_device(self, device_id):
        """Remove a device ID from the DEVICES panel."""
        all_items = self.device_listbox.get(0, tk.END)
        for i, item in enumerate(all_items):
            if device_id in item:
                self.device_listbox.delete(i)   # Delete by index
                break

    def _add_room(self, room_name):
        """Add a room name to the ROOMS panel if not already listed."""
        existing = self.room_listbox.get(0, tk.END)
        if room_name not in existing:
            self.room_listbox.insert(tk.END, room_name)

# ─── Entry Point ──────────────────────────────────────────

def main():
    root = tk.Tk()           # Create the main Tkinter window
    app  = LynkApp(root)     # Build the GUI and start connecting
    root.mainloop()          # Hand control to Tkinter — runs until window closes

if __name__ == "__main__":
    main()