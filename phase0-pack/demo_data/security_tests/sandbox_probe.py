"""Sandbox isolation probe (defensive test fixture for demo step 3).

Run this INSIDE the sandbox through the run_python tool. It only tries to reach things a
locked-down sandbox must NOT be able to reach, and prints BLOCKED or NOT BLOCKED for each.
Nothing is sent anywhere: every attempt is a bare connection / name lookup / file write.
Expected result in a correct sandbox: every line says BLOCKED.
"""
import socket
import urllib.request


def attempt(label, fn):
    try:
        fn()
        print("NOT BLOCKED  %s" % label)
    except Exception as e:  # noqa: BLE001
        print("BLOCKED      %s  (%s)" % (label, type(e).__name__))


def http():
    urllib.request.urlopen("http://example.com", timeout=3)


def tcp():
    s = socket.create_connection(("8.8.8.8", 53), timeout=3)
    s.close()


def dns():
    socket.gethostbyname("example.com")


def write_outside_workspace():
    with open("/etc/sandbox_probe.txt", "w") as f:
        f.write("probe")


def read_outside_workspace():
    open("/etc/shadow").read()


attempt("HTTP request to the internet", http)
attempt("raw TCP connection to 8.8.8.8:53", tcp)
attempt("DNS lookup of example.com", dns)
attempt("write a file outside /out", write_outside_workspace)
attempt("read /etc/shadow", read_outside_workspace)
