from pathlib import Path
import sys
source = Path(__file__).resolve().parents[2] / 'run_checks.py'
text = source.read_text()
old = "output = root/'experiments/geographic_selection/artifacts'"
assert text.count(old) == 1
text = text.replace(old, 'output = Path(' + repr(str(Path(__file__).resolve().parent)) + ')')
text = text.replace("[sys.executable, '-m', 'experiments.geographic_selection.run_checks', *sys.argv[1:]]", "[sys.executable, *sys.argv]")
exec(compile(text, str(source), 'exec'), {'__name__':'__main__', '__file__':str(source), '__package__':'experiments.geographic_selection'})
