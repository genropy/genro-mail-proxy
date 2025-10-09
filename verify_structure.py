from pathlib import Path

def walk(p: Path, prefix=""):
    for child in sorted(p.iterdir()):
        rel = child.relative_to(p.parent if p.name else p)
        print(rel)
        if child.is_dir():
            for cc in sorted(child.rglob("*")):
                print(cc.relative_to(p.parent if p.name else p))

if __name__ == "__main__":
    base = Path(".")
    for item in ["async_mail_service","tests","docs",".github","Dockerfile","docker-compose.yml","main.py","requirements.txt","readthedocs.yml","README.md"]:
        path = base / item
        exists = path.exists()
        print(f"{item}: {'OK' if exists else 'MISSING'}")
