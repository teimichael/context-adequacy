"""Check the two worked examples without requiring Java or benchmark data."""
from pathlib import Path

from graphs import (
    CE_G, CE_H, CE_T, COUNTEREXAMPLE_CONTEXT, F_SAMPLING, F_UNKNOWN,
    M_ANNOTATE, M_COUNT, M_HASMORE, MOTIVATING_CONTEXT,
    counterexample_graph, counterexample_index, motivating_graph, motivating_index,
)
from lacuna.criterion.closure import analyse
from lacuna.criterion.conformance import check as check_references
from lacuna.criterion.frame import frame
from lacuna.criterion.properties import build_seed
from lacuna.criterion.report import render_verdict
from lacuna.criterion.verdict import decide, VerdictValue
from lacuna.criterion.witness import canonical_witness
from lacuna.model.approx import DEFAULT_APPROXIMATION, LEVELS, Property, FrameDefinition
from lacuna.model.identity import EdgeKind


def check():
    graph = motivating_graph()
    target_frame = frame(M_HASMORE, motivating_index(), DEFAULT_APPROXIMATION.frame)
    seed = build_seed(graph, M_HASMORE, target_frame, Property.P1_CALLER_BEHAVIOUR)
    result = analyse(graph, seed.seed, MOTIVATING_CONTEXT, depth_cap=3, levels=LEVELS)
    verdict = decide(result, depth_cap=3)
    witness = canonical_witness(graph, result)
    if verdict.value != VerdictValue.INADEQUATE or set(result.omit) != {M_ANNOTATE, M_COUNT, F_SAMPLING, F_UNKNOWN}:
        raise AssertionError("Pagination example omissions changed")
    rendered = render_verdict(graph, M_HASMORE, Property.P1_CALLER_BEHAVIOUR,
                              DEFAULT_APPROXIMATION, result, verdict, witness, compact=True)
    if rendered.strip() != Path(__file__).with_name("expected-verdict.txt").read_text().strip():
        raise AssertionError("Pagination example differs from the manuscript report")
    counter = counterexample_graph()
    naive = analyse(counter, [CE_T], COUNTEREXAMPLE_CONTEXT, depth_cap=8)
    if not naive.adequate or CE_G in naive.slice_:
        raise AssertionError("The current-body slice must omit the new callee")
    if CE_G not in frame(CE_T, counterexample_index(), FrameDefinition.WIDE):
        raise AssertionError("The wide frame must include the visible callee")
    if check_references([CE_G], COUNTEREXAMPLE_CONTEXT).conformant:
        raise AssertionError("The new call outside the context must fail conformance")
    larger = analyse(counterexample_graph(with_h=True), [CE_T],
                     set(COUNTEREXAMPLE_CONTEXT) | {CE_G}, depth_cap=8)
    if larger.adequate or CE_H not in larger.omit:
        raise AssertionError("Adding the callee must expose its omitted dependency")
    print("Worked examples: passed")


if __name__ == "__main__":
    check()
