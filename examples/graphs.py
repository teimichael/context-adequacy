from __future__ import annotations
from lacuna.model.edg import ElementDependenceGraph, ElementRecord
from lacuna.model.identity import EdgeKind as K
from lacuna.model.index import MemberInfo, MethodInfo, ProgramIndex, TypeInfo
RP = 'T:com.acme.query.ResultPage'
SC = 'T:com.acme.analytics.StatsCollector'
QE = 'T:com.acme.query.QueryExecutor'
PR = 'T:com.acme.query.PageRequest'
BK = 'T:com.acme.query.Backend'
F_ITEMS = 'F:com.acme.query.ResultPage.items:Ljava/util/List'
F_OFFSET = 'F:com.acme.query.ResultPage.offset:I'
F_LIMIT = 'F:com.acme.query.ResultPage.limit:I'
F_TOTAL = 'F:com.acme.query.ResultPage.totalCount:I'
F_UNKNOWN = 'F:com.acme.analytics.StatsCollector.TOTAL_UNKNOWN:Ljava/lang/Integer'
F_SAMPLING = 'F:com.acme.analytics.StatsCollector.sampling:Z'
M_HASMORE = 'M:com.acme.query.ResultPage#hasMore()Z'
M_SETTOTAL = 'M:com.acme.query.ResultPage#setTotalCount(I)V'
M_ANNOTATE = 'M:com.acme.analytics.StatsCollector#annotate(Lcom/acme/query/ResultPage;Lcom/acme/query/Backend;)V'
M_RUN = 'M:com.acme.query.QueryExecutor#run(Lcom/acme/query/PageRequest;)Lcom/acme/query/ResultPage;'
M_COUNT = 'M:com.acme.query.Backend#count()I'
M_TEST = 'M:com.acme.query.PaginationTest#testLastPage()V'
RP_SRC = 'core/src/main/java/com/acme/query/ResultPage.java'
SC_SRC = 'analytics/src/main/java/com/acme/analytics/StatsCollector.java'
QE_SRC = 'core/src/main/java/com/acme/query/QueryExecutor.java'
MOTIVATING_CONTEXT: frozenset[str] = frozenset({RP, F_ITEMS, F_OFFSET, F_LIMIT, F_TOTAL, M_HASMORE, M_SETTOTAL, PR, QE, M_RUN, M_TEST})
MOTIVATING_CALLS: tuple[tuple[str, str], ...] = ((M_ANNOTATE, M_SETTOTAL), (M_ANNOTATE, M_COUNT), (M_TEST, M_RUN), (M_TEST, M_HASMORE))
MOTIVATING_LINES: dict[str, tuple[str, int]] = {RP: (RP_SRC, 3), F_ITEMS: (RP_SRC, 4), F_OFFSET: (RP_SRC, 4), F_LIMIT: (RP_SRC, 4), F_TOTAL: (RP_SRC, 5), M_SETTOTAL: (RP_SRC, 12), M_HASMORE: (RP_SRC, 14), SC: (SC_SRC, 3), F_UNKNOWN: (SC_SRC, 4), F_SAMPLING: (SC_SRC, 5), M_ANNOTATE: (SC_SRC, 10), QE: (QE_SRC, 3), M_RUN: (QE_SRC, 12), PR: ('core/src/main/java/com/acme/query/PageRequest.java', 3), BK: ('core/src/main/java/com/acme/query/Backend.java', 3), M_COUNT: ('core/src/main/java/com/acme/query/Backend.java', 4), M_TEST: ('core/src/test/java/com/acme/query/PaginationTest.java', 20)}

def motivating_graph() -> ElementDependenceGraph:
    g = ElementDependenceGraph()
    for eid, (src, line) in MOTIVATING_LINES.items():
        g.add_element(ElementRecord(id=eid, source_file=src, start_line=line))
    g.add_edge(M_HASMORE, F_ITEMS, K.HEAP_READ)
    g.add_edge(M_HASMORE, F_LIMIT, K.HEAP_READ)
    g.add_edge(F_TOTAL, M_SETTOTAL, K.HEAP_WRITE)
    g.add_edge(M_ANNOTATE, F_SAMPLING, K.HEAP_READ)
    g.add_edge(M_ANNOTATE, F_UNKNOWN, K.HEAP_READ)
    for caller, callee in MOTIVATING_CALLS:
        g.add_edge(caller, callee, K.RETURN)
        g.add_edge(callee, caller, K.CALL)
    return g

def motivating_index() -> ProgramIndex:
    fields = (F_ITEMS, F_OFFSET, F_LIMIT, F_TOTAL)
    return ProgramIndex(types={RP: TypeInfo(id=RP, fields=fields, methods=(M_HASMORE, M_SETTOTAL)), SC: TypeInfo(id=SC, fields=(F_UNKNOWN, F_SAMPLING), methods=(M_ANNOTATE,)), QE: TypeInfo(id=QE, methods=(M_RUN,)), PR: TypeInfo(id=PR), BK: TypeInfo(id=BK, methods=(M_COUNT,))}, methods={M_HASMORE: MethodInfo(id=M_HASMORE, owner=RP, return_type=None), M_SETTOTAL: MethodInfo(id=M_SETTOTAL, owner=RP), M_ANNOTATE: MethodInfo(id=M_ANNOTATE, owner=SC, parameter_types=(RP, BK)), M_RUN: MethodInfo(id=M_RUN, owner=QE, parameter_types=(PR,), return_type=RP), M_COUNT: MethodInfo(id=M_COUNT, owner=BK)}, members={f: MemberInfo(id=f) for f in fields + (F_UNKNOWN, F_SAMPLING)})
CE_MAIN = 'M:p.Prog#main()V'
CE_CALLER = 'M:p.Prog#caller()I'
CE_T = 'M:p.Prog#t()I'
CE_G = 'M:p.Prog#g()I'
CE_H = 'M:p.Prog#h()I'
CE_TYPE = 'T:p.Prog'
COUNTEREXAMPLE_CONTEXT: frozenset[str] = frozenset({CE_MAIN, CE_CALLER, CE_T, CE_TYPE})

def counterexample_graph(*, with_h: bool=False) -> ElementDependenceGraph:
    g = ElementDependenceGraph()
    src = 'src/main/java/p/Prog.java'
    for eid, line in [(CE_TYPE, 1), (CE_MAIN, 2), (CE_CALLER, 3), (CE_T, 4), (CE_G, 5), (CE_H, 6)]:
        g.add_element(ElementRecord(id=eid, source_file=src, start_line=line))
    g.add_edge(CE_MAIN, CE_CALLER, K.RETURN)
    g.add_edge(CE_CALLER, CE_T, K.RETURN)
    g.add_edge(CE_T, CE_CALLER, K.CALL)
    g.add_edge(CE_CALLER, CE_MAIN, K.CALL)
    if with_h:
        g.add_edge(CE_G, CE_H, K.RETURN)
    return g

def counterexample_index() -> ProgramIndex:
    return ProgramIndex(types={CE_TYPE: TypeInfo(id=CE_TYPE, methods=(CE_MAIN, CE_CALLER, CE_T, CE_G, CE_H))}, methods={m: MethodInfo(id=m, owner=CE_TYPE) for m in (CE_MAIN, CE_CALLER, CE_T, CE_G, CE_H)})
