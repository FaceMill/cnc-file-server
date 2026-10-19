"""Demo only: a FANUC on a serial device server at 192.168.50.40:4001.
Stays connected like a real machine. Punches one program 5 s after the Pi
connects, and pauses every send once with XOFF for 2 s."""
import socket, threading, time

PROGRAM = (b"\x00\x00\x12%\r\nO2001(PUNCHED FROM FANUC DEMO)\r\nG90G54G00X0Y0\r\nS1200M03\r\n"
           b"G01Z-1.0F200\r\nX50.0\r\nY50.0\r\nX0\r\nY0\r\nG00Z25.0\r\nM30\r\n%\x14\x00")


def serve(conn):
    punched = False
    connected = time.time()
    got = 0
    conn.settimeout(0.5)
    while True:
        try:
            d = conn.recv(256)
            if not d:
                return
            got += len(d)
            if got > 1500 and got - len(d) <= 1500:
                conn.sendall(b"\x13"); time.sleep(2); conn.sendall(b"\x11")
        except socket.timeout:
            got = 0
            if not punched and time.time() - connected > 5:
                conn.sendall(PROGRAM); punched = True
        except OSError:
            return


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("192.168.50.40", 4001))
srv.listen(4)
while True:
    c, _ = srv.accept()
    threading.Thread(target=serve, args=(c,), daemon=True).start()
