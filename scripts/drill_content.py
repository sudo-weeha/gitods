"""演练内容：经真实服务创建项目、ZIP 导入、分支与相对图片。

仅用于容器验收/恢复演练，显式执行，不进入任何自动启动流程。
用法：docker compose run --rm web python scripts/drill_content.py
"""
import io
import os
import zipfile

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

from catalog.models import Branch, Project  # noqa: E402
from catalog.services.branches import create_branch, set_default_branch  # noqa: E402
from catalog.services.entries import upload_files  # noqa: E402
from catalog.services.imports import prepare_zip_import, publish_import  # noqa: E402
from catalog.services.projects import create_group, create_project  # noqa: E402

# 1×1 红色 PNG（真实可被 Pillow 验证的图片）
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082"
)


def _zip(files: dict) -> SimpleUploadedFile:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            archive.writestr(name, data)
    return SimpleUploadedFile("drill.zip", payload.getvalue(), content_type="application/zip")


def main() -> None:
    if Project.objects.filter(slug="drill-app").exists():
        project = Project.objects.get(slug="drill-app")
        if project.branches.filter(name="release-1.0").exists():
            print("演练内容已存在，跳过")
            return
        owner = get_user_model().objects.filter(username="drill-admin").first()
        _finish_release(project, owner)
        return
    owner = (
        get_user_model().objects.filter(username="drill-admin").first()
        or get_user_model().objects.create_superuser(
            "drill-admin", password="Drill-Admin-Only-42"
        )
    )
    group = create_group(name="演练分组", slug="drill", description="容器验收")
    project = create_project(
        group=group, name="演练项目", slug="drill-app",
        description="备份恢复演练：两分支不同 README、相对图片、目录与 ZIP。",
        language="Python",
    )
    main_branch = project.default_branch

    readme_main = (
        "# 演练项目（main）\n\n"
        "## 结构\n\n"
        "| 分支 | 用途 |\n| --- | --- |\n| main | 演练基准 |\n| release-1.0 | 恢复后校验 |\n\n"
        "![红点](assets/dot.png)\n\n"
        "- [架构说明](docs/架构说明.md)\n"
        "```python\nprint('main 分支')\n```\n"
    )
    draft = prepare_zip_import(
        project_id=project.id, branch_id=main_branch.id,
        expected_revision=main_branch.revision, owner=owner,
        uploaded_file=_zip({
            "README.md": readme_main,
            "assets/dot.png": PNG_1PX,
            "docs/架构说明.md": "# 架构\n\nmain 分支的说明文档。\n",
            "src/service.py": "def run():\n    return 'main'\n",
        }),
    )
    publish_import(draft_id=draft.id, owner=owner)

    main_branch.refresh_from_db()  # 发布已递增 revision，复制需用最新基准
    _finish_release(project, owner)
    print("演练内容完成：分组 drill，项目 drill-app，分支 main + release-1.0（默认）")


def _finish_release(project, owner):
    main_branch = project.branches.get(name="main")
    main_branch.refresh_from_db()
    release = create_branch(
        project_id=project.id, name="release-1.0",
        source_branch_id=main_branch.id, expected_revision=main_branch.revision,
    )
    upload_files(
        branch_id=release.id, expected_revision=release.revision, parent_path="",
        files=[SimpleUploadedFile("README.md", "# 演练项目（release-1.0）\n\n恢复后此 README 应与 main 不同。\n".encode())],
        overwrite=True,
    )
    set_default_branch(project_id=project.id, branch_id=release.id)


if __name__ == "__main__":
    main()
