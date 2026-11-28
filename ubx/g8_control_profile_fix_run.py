# 中文：先构建并部署固定提交的夹具修正，通过后执行EP32控制开关对照；任一阶段失败即停止。
# English: Build/deploy the pinned fixture correction, then run EP32 control A/B; stop on any failed stage.
from pathlib import Path
for entry in ['g8_control_profile_build.py','g8_control_run.py']:
    path=Path(__file__).with_name(entry)
    exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'),{'__name__':'__main__','__file__':str(path)})
