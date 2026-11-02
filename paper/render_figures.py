"""Preserve original numeric figures and regenerate the new controlled comparison."""
from pathlib import Path
import shutil
from render_controlled_figure import main
HERE=Path(__file__).resolve().parent
if __name__=='__main__':
    for name in ['figure1_collision','figure2_calibration']:
        for ext in ['pdf','svg','png']:
            shutil.copy2(HERE.parent/'supplement/v0.1.1_paper/figures'/f'{name}.{ext}',HERE/'figures'/f'{name}.{ext}')
    main()
