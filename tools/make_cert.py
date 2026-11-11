# -*- coding: utf-8 -*-
"""生成自签证书（certs/cert.pem, certs/key.pem）。

手机浏览器调用麦克风要求「安全上下文」：localhost 可以，但 http://192.168.x.x 不行。
以 https 启动服务后，还需浏览器认可安全上下文并授予麦克风权限。
部分手机浏览器需要在系统中信任证书，不能仅靠点击“继续访问”。
"""
import datetime
import ipaddress
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CERT_DIR = os.path.join(ROOT, "certs")


def local_ips():
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ":" in ip:
                continue
            ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def main():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    if not os.path.isdir(CERT_DIR):
        os.makedirs(CERT_DIR)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"qc-demo.local")])
    san = [x509.DNSName(u"localhost"), x509.DNSName(u"qc-demo.local")]
    for ip in local_ips():
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.utcnow()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))

    cert_path = os.path.join(CERT_DIR, "cert.pem")
    key_path = os.path.join(CERT_DIR, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    # The phone can download this public certificate through the existing
    # static server. Never copy the private key into the frontend directory.
    public_path = os.path.join(ROOT, "frontend", "qc-demo-ca.cer")
    with open(public_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.DER))
    print("证书已生成：", cert_path)
    print("私钥已生成：", key_path)
    print("包含地址：", ", ".join(["localhost"] + local_ips()))
    print("\n手机访问：https://<上面的局域网IP>:8760  （录音还需浏览器信任证书并允许麦克风）")


if __name__ == "__main__":
    sys.exit(main() or 0)
