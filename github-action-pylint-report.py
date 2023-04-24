#!/usr/bin/env python3
from pylint.reporters import JSONReporter
from pylint.lint import Run
from glob import glob
from io import StringIO
import pandas as pd
import json
import os
import sys


def del_dict_keys(r, *args: str):
    for arg in args:
        r.pop(arg, None)


def pylint_project(module_path: str, branch_url: str):
    pylint_options = []
    pylint_overview = []
    pylint_results = []
    pylint_messages = {}
    glob_pattern = os.path.join(module_path, "**", "*.py")
    score_sum = 0.0
    smell_sum = 0
    for filepath in glob(glob_pattern, recursive=True):
        if filepath[:11] == "__init__.py":
            continue
        reporter_buffer = StringIO()
        results = Run(
            [filepath] + pylint_options,
            reporter=JSONReporter(reporter_buffer),
            do_exit=False,
        )
        score = results.linter.stats.global_note
        file_results = json.loads(reporter_buffer.getvalue())
        if not file_results:
            continue
        for r in file_results:
            sym = r["symbol"]
            typ = r['type']
            if sym in ["duplicate-except", "super-init-not-called", "attribute-defined-outside-init"]:
                typ = "notice"
            else:
                pylint_messages[sym] = 0
            file=r['path']
            print(
                f"::{typ} file={file},line={r['line']},endLine={r['endLine']},title={sym}::{r['message']}"
            )
            strip = len(module_path)+1
            link=f"[{file[strip:]}]({branch_url}/{file}#L{r['line']})"
            del_dict_keys(
                r, "module", "column", "endColumn", "message-id", "endLine", "type", "line"
            )
            r["obj"] = r["obj"][:32]
            r["path"] = link
            r["symbol"] = sym[:32]
            r["message"] = r["message"][:96]
        pylint_results.extend(file_results)
        smells = len(file_results)
        smell_sum += smells
        score_sum += score

        pylint_overview.append(
            {
                "filepath": filepath,
                "smells": smells,
                "symbols": " ".join(pylint_messages.keys()),
                "score": float("{:0.3f}".format(score)),
            }
        )
    avg_score = score_sum / len(pylint_overview)
    pylint_overview.append(
        {
            "filepath": "total",
            "smells": smell_sum,
            "symbols": "",
            "score": float("{:0.3f}".format(avg_score)),
        }
    )
    return pd.DataFrame(pylint_overview), pd.DataFrame(pylint_results)  # , avg_score



def main(module_dir, output_file, branch_url):
    panda_overview, panda_results = pylint_project(module_dir, branch_url)
    print("### Overview")
    print(panda_overview.to_markdown())
    print("\n### Results")
    print(panda_results.to_markdown())

    summary_file = output_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fp:
            fp.write(panda_results.to_markdown())

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])