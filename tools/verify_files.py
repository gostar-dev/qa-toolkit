"""받은 파일이 빠짐없이, 바뀐 것 없이 그대로인지 확인한다.

MANIFEST.sha256 에는 배포할 때의 파일마다 SHA-256 지문이 적혀 있다.
파일 내용이 한 글자라도 바뀌면 지문이 달라진다.
  python tools/verify_files.py

결과: 빠진 파일, 내용이 바뀐 파일, 목록에 없는 파일을 보여준다.
reports/ 와 data/ 는 실행할 때마다 다시 만들어지는 결과물이라 검사에서 뺀다.
.github/ 는 GitHub 화면에서 직접 만드는 설정 파일이라 검사에서 뺀다.
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "MANIFEST.sha256"
SKIP_DIRS = {"reports", "data", ".git", ".github", "__pycache__", ".pytest_cache"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p == MANIFEST:
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield rel.as_posix()


def build():
    lines = [f"{sha256(ROOT / rel)}  {rel}" for rel in tracked_files()]
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"MANIFEST.sha256 생성: 파일 {len(lines)}개")
    return 0


def verify():
    if not MANIFEST.exists():
        print("MANIFEST.sha256 이 없음. 저장소 맨 위에 있어야 한다.")
        return 1
    expected = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, rel = line.split("  ", 1)
            expected[rel] = digest
    missing, changed = [], []
    for rel, digest in expected.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        elif sha256(p) != digest:
            changed.append(rel)
    extra = [rel for rel in tracked_files() if rel not in expected]

    ok = len(expected) - len(missing) - len(changed)
    print(f"확인한 파일 {len(expected)}개: 일치 {ok}개, 빠짐 {len(missing)}개, 내용 바뀜 {len(changed)}개")
    for rel in missing:
        print(f"  [빠짐] {rel}")
    for rel in changed:
        print(f"  [바뀜] {rel}")
    for rel in extra:
        print(f"  [목록에 없음] {rel}")
    if not missing and not changed:
        print("받은 파일이 전부 그대로 있습니다.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(build() if "--build" in sys.argv else verify())
