#!/usr/bin/env python3
import json
import os
import sys
from glob import glob
from io import StringIO
from typing import List

from pylint.lint import Run  # type: ignore
from pylint.reporters import JSONReporter  # type: ignore

import pandas as pd


def del_dict_keys(r, *args):
    for arg in args:
        r.pop(arg, None)

def cleanup_results_dict(r, sym):
    del_dict_keys(
        r,
        "module",
        "column",
        "endColumn",
        "message-id",
        "endLine",
        "type",
        "line",
    )
    r["symbol"] = sym[:32]
    r["message"] = r["message"][:96]
    try:
        dotpos = r["obj"].rindex(".") + 1
    except ValueError:
        dotpos = 0
    r["obj"] = r["obj"][dotpos:][:16]


def pylint_project(module_path: str, branch_url: str):
    pylint_options: List[str] = []
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
            typ = r["type"]
            if sym in [
                "duplicate-except",
                "super-init-not-called",
                "attribute-defined-outside-init",
            ]:
                typ = "notice"
            else:
                pylint_messages[sym] = 0
            filepath = r["path"]
            print(
                f"::{typ} file={filepath},line={r['line']},endLine={r['endLine']},"
                f"title={sym}::{r['message']}"
            )

            # Use a short link text: filname without directory and the .py extension:
            try:
                slashpos = filepath.rindex("/") + 1
            except ValueError:
                slashpos = 0
            file = filepath[slashpos:]
            linktext = file.removesuffix(".py")   # type: ignore  # run this on >= 3.9

            r["path"] = f"[{linktext}]({branch_url}/{filepath}#L{r['line']})"
            cleanup_results_dict(r, sym)

        pylint_results.extend(file_results)
        smells = len(file_results)
        smell_sum += smells
        score_sum += score

        pylint_overview.append(
            {
                "filepath": filepath,
                "smells": smells,
                "symbols": " ".join(pylint_messages.keys()),
                "score": round(score, 3),
            }
        )
    avg_score = score_sum / len(pylint_overview)
    pylint_overview.append(
        {
            "filepath": "total",
            "smells": smell_sum,
            "symbols": "",
            "score": round(avg_score, 3),
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
    py_module_dir = sys.argv[1]
    gh_output_file = sys.argv[2]
    gh_branch_url = sys.argv[3]

    main(sys.argv[1], sys.argv[2], sys.argv[3])
