"""Construit un bundle de certificats = certifi + magasin Windows (ROOT et CA).

Outil de DÉVELOPPEMENT LOCAL, jamais importé par le service. À utiliser seulement
si Python refuse un certificat que Windows accepte (voir docs/TLS_LOCAL.md). La
vérification TLS reste ACTIVE : on ajoute des autorités de confiance, on n'en
désactive aucune.

    python scripts/ca_bundle_windows.py [chemin_de_sortie]
"""
import ssl
import sys
from pathlib import Path

import certifi


def main() -> None:
    sortie = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".virelyon" / "ca-bundle.pem"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    pem = [Path(certifi.where()).read_text(encoding="utf-8")]
    for magasin in ("ROOT", "CA"):
        for der, encodage, _ in ssl.enum_certificates(magasin):  # Windows uniquement
            if encodage == "x509_asn":
                pem.append(ssl.DER_cert_to_PEM_cert(der))
    sortie.write_text("\n".join(pem), encoding="utf-8")
    print(f"{sortie} — {sum(p.count('BEGIN CERTIFICATE') for p in pem)} certificats")


if __name__ == "__main__":
    main()
