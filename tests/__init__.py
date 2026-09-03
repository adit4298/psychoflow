"""Test suites for the hackathon vision/IoT branch.

The repo has no pytest (12 phases have deliberately avoided it — every
module carries hand-scored assertions run by `python -m <module>`). These
suites keep that idiom: plain asserts, a `main()` returning a pass/fail
count, runnable as `python -m tests.<name>`.

The owning modules' `__main__` blocks call into these so the done-bar
command and the test suite are the SAME assertions, not two copies that
can drift.
"""
