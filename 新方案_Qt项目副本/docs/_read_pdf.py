import fitz
import sys

pdf_path = "c:/Users/a/Desktop/ai_pipeline_prototype/新方案_Qt项目副本/docs/RTBasic编程手册V1.1.5.pdf"
doc = fitz.open(pdf_path)
print(f"Total pages: {doc.page_count}")
print("=" * 60)

start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else min(10, doc.page_count)

for i in range(start, min(end, doc.page_count)):
    page = doc[i]
    text = page.get_text()
    print(f"\n{'='*60}")
    print(f"PAGE {i+1}")
    print(f"{'='*60}")
    print(text[:3000])

doc.close()
