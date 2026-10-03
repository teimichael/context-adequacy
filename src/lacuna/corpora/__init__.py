from lacuna.corpora.base import Corpus, Instance, InstanceId
from lacuna.corpora.contextbench import ContextBench
from lacuna.corpora.multi_swe_bench import MultiSweBenchJava
CORPORA = {'multi-swe-bench': MultiSweBenchJava}
COMPARISON_CORPORA = {'contextbench': ContextBench}
