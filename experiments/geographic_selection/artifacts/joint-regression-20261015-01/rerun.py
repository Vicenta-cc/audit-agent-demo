from pathlib import Path
import sys
root=Path(__file__).resolve().parents[4]
source=root/'experiments/geographic_selection/run_checks.py'
text=source.read_text()
text=text.replace("output = root/'experiments/geographic_selection/artifacts'", "output = root/'experiments/geographic_selection/artifacts/joint-regression-20261015-01'")
text=text.replace("[sys.executable, '-m', 'experiments.geographic_selection.run_checks', *sys.argv[1:]]", "[sys.executable, 'experiments/geographic_selection/artifacts/joint-regression-20261015-01/rerun.py', *sys.argv[1:]]")
scope={'__name__':'__main__','__package__':'experiments.geographic_selection','__file__':str(source)}
exec(compile(text,str(source),'exec'),scope)
