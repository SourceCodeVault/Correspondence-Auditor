#!/usr/bin/env python3
"""
Correspondence Auditor — Multi-Stage Audit Runner
====================================================
Runs a 3-gate audit pipeline against simulated persona outputs:
  Gate 1: Sanity check (structural validation)
  Gate 2: Fact verification (claims vs source material)
  Gate 3: Logic audit (reasoning consistency)

Usage:
  python run_audit.py
"""
import sys
import os
import json
import yaml
import shutil
import re
from pathlib import Path
from datetime import datetime
import concurrent.futures

# --- PATH SETUP ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# --- IMPORTS ---
try:
    from steps.audit_engine import run_audit_gauntlet
except ImportError as e:
    print(f"❌ Critical Error: Could not import 'audit_engine' from 'steps'.")
    print(f"   Details: {e}")
    sys.exit(1)

from shared.ui_utils import print_header, print_success, print_failure, print_warning, print_info

# --- CONFIG ---
MODULE_DIR = Path(__file__).parent
OUTPUT_DIR = MODULE_DIR / "output"
TARGET_MODEL = None


# --- SHARED CLEANING UTILS ---
def clean_source_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # Strip MD links
    text = re.sub(r"<(https?://[^>]+)>", "", text)  # Strip raw URLs
    patterns_to_strip = [
        r"### 🎯 Detected Sales Motion.*?(?=\n#|\n---)",
        r"## (Product|Platform|Company) (Links|Overview|List).*?(?=\n#|\n---)",
    ]
    for pattern in patterns_to_strip:
        text = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def load_ground_truth(run_dir: Path) -> tuple[str, str]:
    """
    Robust Ground Truth Loader: Checks Manifest -> Checks Input_*.json -> Deduplicates.
    Returns (source_text, company_name) or None on failure.
    """
    try:
        prov_dir = run_dir / "_provenance"
        manifest_path = prov_dir / "manifest.json"
        input_filename = None

        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                input_filename = manifest.get("input_source")
            except Exception:
                pass

        candidate_paths = []
        if input_filename:
            candidate_paths.append(run_dir / input_filename)
            candidate_paths.append(prov_dir / input_filename)
            if not input_filename.startswith("input_"):
                candidate_paths.append(run_dir / f"input_{input_filename}")

        if not any(p.exists() for p in candidate_paths):
            candidate_paths = (
                list(run_dir.glob("input_*.json"))
                + list(run_dir.glob("input_*.yaml"))
                + list(run_dir.glob("input_*.yaml.txt"))
                + list(run_dir.glob("input_*.txt"))
            )

        for p in candidate_paths:
            if p.exists():
                print_info(f"Loading Ground Truth: {p.name}")
                company_name = p.stem.replace("input_", "").split("_")[0]

                content = p.read_text(encoding="utf-8")
                try:
                    if p.name.endswith((".yaml.txt", ".yaml", ".yml", ".txt")):
                        data = yaml.safe_load(content)
                    else:
                        data = json.loads(content)
                except Exception as e:
                    print_failure(f"Failed to parse {p.name}: {e}")
                    continue

                full_material = data.get("material", "")

                if isinstance(full_material, list):
                    full_material = "\n".join(full_material)

                # Deduplicate by evidence source
                sources = data.get("evidence_sources", [])
                unique_blocks = []
                seen_urls = set()
                if sources:
                    for src in sources:
                        url = src.get("url")
                        category = src.get("category")
                        if url and url not in seen_urls:
                            pattern = rf"(--- \[SOURCE: {category} \|.*?---.*?)(?=--- \[SOURCE:|$)"
                            match = re.search(pattern, full_material, re.DOTALL)
                            if match:
                                unique_blocks.append(match.group(1).strip())
                                seen_urls.add(url)

                final_text = "\n\n".join(unique_blocks) if unique_blocks else full_material
                return clean_source_text(final_text), company_name

        print_failure("Could not locate Ground Truth file.")
        return None

    except Exception as e:
        print_failure(f"Error loading Ground Truth: {e}")
        return None


def select_folder_from_dir(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    folders = sorted(
        [f for f in directory.iterdir() if f.is_dir()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not folders:
        return None
    print(f"\nSelect Simulation Run:")
    for i, f in enumerate(folders[:19]):
        print(f"  [{i + 1}] {f.name}")
    choice = input(f"> (1-{min(len(folders), 19)}): ").strip()
    try:
        idx = int(choice) - 1
        return folders[idx] if 0 <= idx < len(folders) else None
    except Exception:
        return None


def get_source_map(r_dir: Path) -> dict:
    """Extract evidence source URL mapping from the run's input file."""
    try:
        prov_dir = r_dir / "_provenance"
        manifest_path = prov_dir / "manifest.json"
        input_filename = None
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            input_filename = manifest.get("input_source")

        candidate_paths = []
        if input_filename:
            candidate_paths.extend(
                [r_dir / input_filename, prov_dir / input_filename, r_dir / f"input_{input_filename}"]
            )
        if not any(p.exists() for p in candidate_paths):
            candidate_paths = (
                list(r_dir.glob("input_*.json"))
                + list(r_dir.glob("input_*.yaml"))
                + list(r_dir.glob("input_*.yaml.txt"))
                + list(r_dir.glob("input_*.txt"))
            )

        for p in candidate_paths:
            if p.exists():
                content = p.read_text(encoding="utf-8")
                data = (
                    yaml.safe_load(content)
                    if p.name.endswith((".yaml.txt", ".yaml", ".yml", ".txt"))
                    else json.loads(content)
                )
                sources = data.get("evidence_sources", [])
                return {src.get("category", "Unknown"): src.get("url", "") for src in sources}
    except Exception:
        pass
    return {}


# --- AUDIT LOOP ---
def run_audit_loop(run_dir: Path, target_model: str, resume: bool):
    result = load_ground_truth(run_dir)
    if not result:
        return
    source_text, company_name = result

    target_files = sorted(list(run_dir.glob("result_*.json")))
    audit_dir = run_dir / "_audit_gauntlet"
    audit_dir.mkdir(exist_ok=True)

    print_info(f"Running Audit on {len(target_files)} files...")
    stats = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIPPED": 0}
    files_to_process = []

    # Pre-flight: skip already-audited files on resume
    for i, f in enumerate(target_files, 1):
        log_path = audit_dir / f"audit_{f.name}"

        if resume and log_path.exists():
            try:
                existing_data = json.loads(log_path.read_text(encoding="utf-8"))
                verdict = existing_data.get("final_verdict")
                if verdict in ["PASS", "FAIL"]:
                    print(f"[{i}/{len(target_files)}] ⏭️  Skipping {f.name} (Already {verdict})")
                    stats["SKIPPED"] += 1
                    if verdict == "PASS":
                        stats["PASS"] += 1
                    if verdict == "FAIL":
                        stats["FAIL"] += 1
                    continue
            except Exception:
                pass

        files_to_process.append((i, f))

    if not files_to_process:
        print_success("\nAll files have already been audited. Nothing to do!")
        return

    def process_file(index, f):
        log_path = audit_dir / f"audit_{f.name}"
        result = run_audit_gauntlet(f, source_text, target_model, company_name)
        log_path.write_text(json.dumps(result, indent=2))
        return index, f, result.get("final_verdict", "ERROR")

    max_workers = 36
    print_info(f"\nLaunching concurrent audit for {len(files_to_process)} files ({max_workers} threads)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_file, idx, f) for idx, f in files_to_process]

        for future in concurrent.futures.as_completed(futures):
            index, f, verdict = future.result()
            if verdict == "PASS":
                print(f"[{index}/{len(target_files)}] ✅ PASS: {f.name}")
                stats["PASS"] += 1
            elif verdict == "FAIL":
                print(f"[{index}/{len(target_files)}] ❌ FAIL: {f.name}")
                stats["FAIL"] += 1
            else:
                print(f"[{index}/{len(target_files)}] 💥 ERROR: {f.name}")
                stats["ERROR"] += 1

    print_header("SUMMARY")
    print(f"Passed: {stats['PASS']} | Failed: {stats['FAIL']} | Skipped: {stats['SKIPPED']} | Errors: {stats['ERROR']}")


# --- ARCHIVE FAILURES ---
def archive_failed_results(run_dir: Path):
    audit_dir = run_dir / "_audit_gauntlet"
    if not audit_dir.exists():
        print_failure("No audit history found.")
        return

    logs = sorted(list(audit_dir.glob("audit_result_*.json")))
    failed_files = []

    for log in logs:
        try:
            data = json.loads(log.read_text(encoding="utf-8"))
            if data.get("final_verdict") != "PASS":
                target_filename = log.name.replace("audit_", "", 1)
                target_file = run_dir / target_filename
                failed_files.append(
                    {"log": log, "target": target_file if target_file.exists() else None}
                )
        except Exception:
            pass

    if not failed_files:
        print_success("No failed files to archive!")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"failed_{timestamp}"
    archive_root = run_dir / "_archive"
    archive_folder = archive_root / folder_name
    archive_folder.mkdir(parents=True, exist_ok=True)

    print(f"\n📦 Archiving {len(failed_files)} failures to: {archive_folder.name}")
    moved_count = 0
    for item in failed_files:
        new_log_name = f"{folder_name}_{item['log'].name}"
        shutil.move(str(item["log"]), str(archive_folder / new_log_name))

        if item["target"]:
            new_target_name = f"{folder_name}_{item['target'].name}"
            shutil.move(str(item["target"]), str(archive_folder / new_target_name))
            print(f"  -> Moved {new_target_name}")
        else:
            print(f"  -> Moved log only (Target missing): {new_log_name}")
        moved_count += 1
    print_success(f"Archived {moved_count} failure sets.")


# --- DASHBOARD VIEWER ---
def view_results(run_dir: Path):
    import webbrowser

    audit_dir = run_dir / "_audit_gauntlet"
    if not audit_dir.exists():
        print_failure("No audit history found.")
        return

    logs = sorted(list(audit_dir.glob("audit_result_*.json")))
    print_info("Generating interactive visual dashboard...")

    dashboard_data = {
        "meta": {
            "run_id": run_dir.name,
            "total": len(logs),
            "source_map": get_source_map(run_dir),
        },
        "stats": {"PASS": 0, "FAIL": 0, "ERROR": 0},
        "records": [],
    }

    for log in logs:
        try:
            data = json.loads(log.read_text(encoding="utf-8"))
            verdict = data.get("final_verdict", "ERROR")
            dashboard_data["stats"][verdict] = dashboard_data["stats"].get(verdict, 0) + 1

            details = []
            if verdict == "FAIL":
                gate2 = data.get("gates", {}).get("gate_2", {})
                if gate2.get("status") == "FAIL":
                    for f in gate2.get("failures", []):
                        details.append(f"Fact Check [{f.get('status')}]: {f.get('claim')}")
                gate3 = data.get("gates", {}).get("gate_3", {})
                if gate3.get("logic_status") == "FAIL":
                    details.append(f"Logic Flaw: {gate3.get('reasoning')}")
                if not details:
                    details.append(data.get("reason", "Unknown Failure"))

            deep_details = {
                "gate_2": data.get("gates", {}).get("gate_2", {}),
                "gate_3": data.get("gates", {}).get("gate_3", {}),
            }
            dashboard_data["records"].append(
                {
                    "filename": data.get("filename", log.name),
                    "verdict": verdict,
                    "details": details,
                    "deep_details": deep_details,
                }
            )
        except Exception:
            dashboard_data["stats"]["ERROR"] += 1
            dashboard_data["records"].append(
                {"filename": log.name, "verdict": "ERROR", "details": ["Corrupt log file"], "deep_details": {}}
            )

    # Build Source Map HTML
    source_map_html = ""
    if dashboard_data["meta"]["source_map"]:
        rows_html = ""
        for cat, url in dashboard_data["meta"]["source_map"].items():
            rows_html += f"""
                <tr>
                    <td class="p-3 font-bold text-slate-700">{cat}</td>
                    <td class="p-3 font-mono text-xs text-blue-600"><a href="{url}" target="_blank" class="hover:underline">{url}</a></td>
                </tr>
            """
        source_map_html = f"""
        <div class="paper-card p-6">
            <h3 class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-4">Evidence Source Mapping</h3>
            <div class="border border-slate-200 rounded overflow-hidden shadow-sm">
                <table class="w-full text-left bg-white text-sm">
                    <thead class="bg-slate-50 text-slate-600"><tr><th class="p-3 border-b w-1/4">Source Category</th><th class="p-3 border-b">URL Documented</th></tr></thead>
                    <tbody class="divide-y divide-slate-100">{rows_html}</tbody>
                </table>
            </div>
        </div>
        """

    safe_json_data = json.dumps(dashboard_data).replace("</", "<\\/")

    # NOTE: Dashboard HTML template is long — kept inline for single-file portability.
    html_content = _build_dashboard_html(dashboard_data, source_map_html, safe_json_data)

    report_path = run_dir / "audit_dashboard.html"
    report_path.write_text(html_content, encoding="utf-8")

    print_success(f"Dashboard generated: {report_path.name}")
    webbrowser.open(f"file://{report_path.resolve()}")


def _build_dashboard_html(dashboard_data: dict, source_map_html: str, safe_json_data: str) -> str:
    """Builds the full HTML string for the audit dashboard."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Gauntlet Audit Viewer</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        body {{ background-color: #f8fafc; color: #0f172a; font-family: 'Inter', sans-serif; }}
        .paper-card {{ background: white; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-radius: 8px; }}
        .accordion-content {{ transition: max-height 0.3s ease-in-out, opacity 0.3s ease-in-out; }}
        .chevron {{ transition: transform 0.3s ease; }}
        .rotate-180 {{ transform: rotate(180deg); }}
    </style>
</head>
<body class="p-8 min-h-screen">
    <div class="max-w-6xl mx-auto space-y-8">
        <div class="flex justify-between items-end border-b-2 border-slate-800 pb-4">
            <div>
                <h1 class="text-3xl font-extrabold text-slate-900 mb-2">Correspondence Audit: Report</h1>
                <p class="text-sm font-mono text-slate-500">{dashboard_data['meta']['run_id']}</p>
            </div>
            <div class="text-right">
                <div class="text-xs font-bold uppercase tracking-widest text-slate-500">Total Scanned</div>
                <div class="text-2xl font-black text-slate-800">{dashboard_data['meta']['total']} Files</div>
            </div>
        </div>
        {source_map_html}
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div class="paper-card p-6 border-l-4 border-l-emerald-500">
                <h3 class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Passed</h3>
                <p class="text-3xl font-black text-emerald-600">{dashboard_data['stats']['PASS']}</p>
            </div>
            <div class="paper-card p-6 border-l-4 border-l-rose-500">
                <h3 class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Failed</h3>
                <p class="text-3xl font-black text-rose-600">{dashboard_data['stats']['FAIL']}</p>
            </div>
            <div class="paper-card p-6 border-l-4 border-l-amber-500">
                <h3 class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Errors</h3>
                <p class="text-3xl font-black text-amber-600">{dashboard_data['stats']['ERROR']}</p>
            </div>
            <div class="paper-card p-4 flex justify-center items-center">
                <canvas id="passFailChart" style="max-height: 100px;"></canvas>
            </div>
        </div>
        <div class="paper-card">
            <div class="px-6 py-4 border-b border-slate-100 bg-slate-50 rounded-t-8 flex justify-between items-center">
                <h3 class="font-bold text-slate-700">Detailed Audit Records</h3>
                <span class="text-xs text-slate-400">Showing full telemetry (click row to collapse)</span>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-sm text-left">
                     <thead class="bg-white sticky top-0 border-b border-slate-200">
                        <tr>
                            <th class="px-6 py-4 font-bold text-slate-900 w-1/3">Filename</th>
                            <th class="px-6 py-4 font-bold text-slate-900 w-24">Verdict</th>
                            <th class="px-6 py-4 font-bold text-slate-900">Failure Summary</th>
                        </tr>
                     </thead>
                     <tbody class="divide-y divide-slate-100" id="records-body"></tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
        const DATA = {safe_json_data};
        function escapeHTML(str) {{
            if (str === null || str === undefined) return '-';
            if (typeof str !== 'string') return String(str);
            return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");
        }}
        document.addEventListener("DOMContentLoaded", function() {{
            const recBody = document.getElementById('records-body');
            DATA.records.forEach((rec, idx) => {{
                const tr = document.createElement('tr');
                let statusBadge = '';
                if(rec.verdict==='PASS') statusBadge='<span class="px-2 py-1 rounded bg-emerald-100 text-emerald-800 text-xs font-bold">PASS</span>';
                else if(rec.verdict==='FAIL') statusBadge='<span class="px-2 py-1 rounded bg-rose-100 text-rose-800 text-xs font-bold">FAIL</span>';
                else statusBadge='<span class="px-2 py-1 rounded bg-amber-100 text-amber-800 text-xs font-bold">ERROR</span>';
                let detailsHTML = rec.verdict==='PASS' ? '<span class="text-slate-400 italic">No anomalies detected</span>' :
                    '<ul class="list-disc pl-4 text-rose-700 text-xs space-y-1">'+rec.details.map(d=>`<li>${{escapeHTML(d)}}</li>`).join('')+'</ul>';
                tr.className="cursor-pointer transition "+(rec.verdict==='FAIL'?"bg-rose-50/20 hover:bg-rose-50/60":"hover:bg-slate-50");
                tr.onclick=()=>{{
                    const detailRow=document.getElementById(`detail-${{idx}}`);
                    const chevron=document.getElementById(`chevron-${{idx}}`);
                    if(detailRow.classList.contains('hidden')){{detailRow.classList.remove('hidden');chevron.classList.add('rotate-180');}}
                    else{{detailRow.classList.add('hidden');chevron.classList.remove('rotate-180');}}
                }};
                tr.innerHTML=`
                    <td class="px-6 py-4 font-mono text-slate-600 text-xs border-r border-slate-100 flex justify-between items-center">
                        ${{escapeHTML(rec.filename)}}
                        <svg id="chevron-${{idx}}" class="w-4 h-4 text-slate-400 chevron rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                    </td>
                    <td class="px-6 py-4">${{statusBadge}}</td>
                    <td class="px-6 py-4">${{detailsHTML}}</td>
                `;
                recBody.appendChild(tr);
                const detailTr=document.createElement('tr');
                detailTr.id=`detail-${{idx}}`;
                detailTr.className="bg-slate-50 border-b-2 border-slate-200 shadow-inner";
                let deepHTML=`<div class="p-6 space-y-6">`;
                if(rec.deep_details.gate_2 && rec.deep_details.gate_2.model_thinking){{
                    deepHTML+=`<div><h4 class="text-xs font-black uppercase tracking-widest text-slate-500 mb-2">Gate 2: Librarian (Thinking Trace)</h4>
                        <div class="p-4 bg-white border border-slate-200 rounded whitespace-pre-wrap font-mono text-xs text-slate-700 max-h-60 overflow-y-auto leading-relaxed shadow-sm">${{escapeHTML(rec.deep_details.gate_2.model_thinking)}}</div></div>`;
                    if(rec.deep_details.gate_2.verdicts){{
                        deepHTML+=`<div><h4 class="text-xs font-black uppercase tracking-widest text-slate-500 mb-2 mt-4">Full Claim Verification Matrix</h4>
                            <div class="border border-slate-200 rounded overflow-hidden shadow-sm">
                                <table class="w-full text-left bg-white text-xs">
                                    <thead class="bg-slate-100 text-slate-600"><tr><th class="p-3 border-b">Persona Claim</th><th class="p-3 border-b">Verdict</th><th class="p-3 border-b">Source</th><th class="p-3 border-b">Source Quote</th></tr></thead>
                                    <tbody class="divide-y divide-slate-100">
                                        ${{rec.deep_details.gate_2.verdicts.map(v=>`
                                            <tr>
                                                <td class="p-3 font-medium text-slate-800">${{escapeHTML(v.claim)}}</td>
                                                <td class="p-3 font-bold ${{v.status==='SUPPORTED'?'text-emerald-600':'text-rose-600'}}">${{escapeHTML(v.status)}}</td>
                                                <td class="p-3 font-mono text-[10px] text-indigo-600 font-bold">${{escapeHTML(v.source||'-')}}</td>
                                                <td class="p-3 font-mono text-[10px] text-slate-500 italic break-words">${{escapeHTML(v.quote)}}</td>
                                            </tr>`).join('')}}
                                    </tbody>
                                </table>
                            </div></div>`;
                    }}
                }}
                if(rec.deep_details.gate_3 && rec.deep_details.gate_3.model_thinking){{
                    deepHTML+=`<div><h4 class="text-xs font-black uppercase tracking-widest text-slate-500 mb-2 mt-4">Gate 3: Psychologist (Thinking Trace)</h4>
                        <div class="p-4 bg-white border border-slate-200 rounded whitespace-pre-wrap font-mono text-xs text-slate-700 max-h-60 overflow-y-auto leading-relaxed shadow-sm">${{escapeHTML(rec.deep_details.gate_3.model_thinking)}}</div></div>`;
                }}
                deepHTML+=`</div>`;
                if(deepHTML===`<div class="p-6 space-y-6"></div>`){{
                    deepHTML=`<div class="p-6 text-sm text-slate-400 italic">No deep telemetry recorded for this run.</div>`;
                }}
                detailTr.innerHTML=`<td colspan="3" class="p-0">${{deepHTML}}</td>`;
                recBody.appendChild(detailTr);
            }});
            const ctx=document.getElementById('passFailChart');
            new Chart(ctx,{{
                type:'doughnut',
                data:{{labels:['Pass','Fail','Error'],datasets:[{{data:[DATA.stats.PASS,DATA.stats.FAIL,DATA.stats.ERROR],backgroundColor:['#10b981','#f43f5e','#f59e0b'],borderWidth:0}}]}},
                options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},cutout:'70%'}}
            }});
        }});
    </script>
</body>
</html>"""


# --- MAIN ---
def main():
    global TARGET_MODEL
    print_header("CORRESPONDENCE AUDIT (MULTI-STAGE)")

    run_dir = select_folder_from_dir(OUTPUT_DIR)
    if not run_dir:
        return

    print("\nSelect Execution Target:\n")
    print("[1] Local Ollama (Sovereign Compute)")
    print("[2] OpenRouter (Zero Data Retention API)")
    print("[3] Cerebras (Wafer-Scale Compute)")

    choice_model = input("> ").strip()
    if choice_model == "1":
        TARGET_MODEL = "local_ollama"
    elif choice_model == "3":
        TARGET_MODEL = "remote_cerebras"
    else:
        TARGET_MODEL = "remote_openrouter"

    while True:
        print(f"\nSelected: {run_dir.name}")
        print("[1] Start New Audit (Overwrite)")
        print("[2] Resume Audit")
        print("[3] View Results (Dashboard)")
        print("[4] Archive Failed Files")
        print("[0] Exit")

        choice = input("> ").strip()
        if choice == "1":
            if (run_dir / "_audit_gauntlet").exists():
                shutil.rmtree(run_dir / "_audit_gauntlet")
            run_audit_loop(run_dir, TARGET_MODEL, resume=False)
        elif choice == "2":
            run_audit_loop(run_dir, TARGET_MODEL, resume=True)
        elif choice == "3":
            view_results(run_dir)
        elif choice == "4":
            archive_failed_results(run_dir)
        elif choice == "0":
            break


if __name__ == "__main__":
    main()
