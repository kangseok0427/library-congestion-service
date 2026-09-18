import argparse
import json
import sys
from pathlib import Path
from .pipeline import DataError, preprocess, refresh, read_current


def main():
    parser = argparse.ArgumentParser(description="T02 전처리 / T08 Excel 갱신")
    parser.add_argument("action", choices=["preprocess", "refresh", "export"])
    parser.add_argument("source", help="입력 xlsx (export일 때 SQLite DB)")
    parser.add_argument("--database", default="data/library.sqlite3")
    parser.add_argument("--sheet")
    parser.add_argument("--source-name", help="임시 업로드 파일 사용 시 원래 파일명")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--partial-date", action="append", help="부분 수집 날짜 YYYY-MM-DD; 반복 가능")
    group.add_argument("--all-complete", action="store_true", help="모든 날짜가 수집 완료임을 확인한 경우에만 사용")
    parser.add_argument("--output", default="output", help="JSON 결과를 저장할 새 폴더 (덮어쓰기 방지)")
    args = parser.parse_args()
    out = Path(args.output)
    try:
        # Reserve output before mutating DB, avoiding successful import then folder conflict.
        out.mkdir(parents=True, exist_ok=False)
        options = dict(sheet=args.sheet, source_name=args.source_name,
                       partial_dates=[] if args.all_complete else args.partial_date)
        if args.action == "preprocess":
            rows, report = preprocess(args.source, **options)
        elif args.action == "refresh":
            refresh(args.source, args.database, **options)
            rows, report = read_current(args.database)
        else:
            rows, report = read_current(args.source)
        (out / "records.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"rows": len(rows), "output": str(out),
                          "integration_status": report.get("integration_status", "not_applicable")}, ensure_ascii=False))
        return 0
    except DataError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        if out.is_dir():
            (out / "errors.json").write_text(json.dumps({**exc.as_dict(), "issues": exc.issues}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    except OSError as exc:
        print(f"파일/폴더 오류: {exc}. 갱신 후 내보내기 실패라면 export로 다시 저장하세요.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
