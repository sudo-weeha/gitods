"""公开页面读取查询：分组导航、项目列表、项目上下文与短只读快照。

项目页同一响应的多个查询使用 PostgreSQL REPEATABLE READ READ ONLY
短事务取得一致快照；普通 atomic() 的 READ COMMITTED 不足以保证
跨查询一致。事务结束后才读取不可变对象内容，不在输出期间持有事务。
"""
import re
from contextlib import contextmanager

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, Q

from catalog.models import Branch, Entry, Group, Project

PAGE_SIZE = 20
RECENT_COUNT = 5

_NUM_SPLIT = re.compile(r"(\d+)")


def natural_key(name: str):
    """统一自然排序键：字母不区分大小写，数字按数值比较。"""
    return [int(part) if part.isdigit() else part.lower() for part in _NUM_SPLIT.split(name)]


def group_nav():
    return Group.objects.annotate(project_count=Count("projects")).order_by("sort_order", "id")


def project_list(*, group_slug=None, q="", sort="updated"):
    qs = Project.objects.select_related("group").all()
    if group_slug:
        qs = qs.filter(group__slug=group_slug)
    if q:
        # 匹配项目名、标识和简介；不做源码全文搜索
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
    if sort == "name":
        qs = qs.order_by("name", "id")
    else:
        qs = qs.order_by("-updated_at", "id")
    return qs


def paginate(queryset, page_number):
    paginator = Paginator(queryset, PAGE_SIZE)
    try:
        return paginator.page(page_number)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def recent_projects():
    return Project.objects.select_related("group").order_by("-updated_at", "id")[:RECENT_COUNT]


def get_project(group_slug, project_slug) -> Project | None:
    return (
        Project.objects.select_related("group", "default_branch")
        .filter(group__slug=group_slug, slug=project_slug)
        .first()
    )


def project_branches(project):
    return list(Branch.objects.filter(project=project).order_by("created_at", "id"))


def find_readme(branch):
    """根目录 README：大小写不敏感匹配 README.md，精确匹配优先，其余按文件名排序。"""
    files = Entry.objects.filter(branch=branch, parent_path="", kind="file")
    candidates = [e for e in files if e.name.lower() == "readme.md"]
    if not candidates:
        return None
    exact = [e for e in candidates if e.name == "README.md"]
    return (exact or sorted(candidates, key=lambda e: e.name))[0]


def list_directory(branch, path):
    """单层目录：目录优先，内部名称自然排序；目录显式保存，支持空目录。"""
    entries = list(Entry.objects.filter(branch=branch, parent_path=path))
    entries.sort(key=lambda e: (0 if e.kind == "dir" else 1, natural_key(e.name)))
    return entries


def get_entry(branch, path) -> Entry | None:
    if not path:
        return None
    return Entry.objects.select_related("blob").filter(branch=branch, path=path).first()


def count_entries(branch) -> int:
    return Entry.objects.filter(branch=branch).count()


@contextmanager
def readonly_snapshot():
    """短 PostgreSQL REPEATABLE READ READ ONLY 事务，跨查询一致快照。

    生产请求不启用 ATOMIC_REQUESTS，atomic() 发出真正的 BEGIN，
    SET TRANSACTION 作为首条语句合法。若已处于外层事务（测试的
    事务包装或嵌套调用），SAVEPOINT 上无法切换隔离级别，退化为
    普通原子块——外层事务本身提供同等或更强的隔离。
    """
    from django.db import connection, transaction

    if connection.in_atomic_block:
        with transaction.atomic():
            yield
        return

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def branch_tree(branch, current_path: str) -> list[dict]:
    """整棵分支树（目录优先、自然排序），供文件导航面板渲染。

    current_path 的祖先目录与该目录本身自动展开，当前条目高亮。
    节点结构：{name, kind, path, open, current, children}。
    """
    nodes: dict[str, dict] = {}

    def ensure_dir(path: str) -> dict:
        node = nodes.get(path)
        if node is None:
            parts = path.split("/")
            parent = ensure_dir("/".join(parts[:-1]))["children"] if len(parts) > 1 else root_children
            node = {
                "name": parts[-1], "kind": "dir", "path": path,
                "open": False, "current": False, "children": [],
            }
            nodes[path] = node
            parent.append(node)
        return node

    root_children: list[dict] = []
    entries = Entry.objects.filter(branch=branch).only("path", "name", "kind")
    for entry in entries:
        if entry.kind == "dir":
            ensure_dir(entry.path)
        else:
            parts = entry.path.split("/")
            parent = ensure_dir("/".join(parts[:-1]))["children"] if len(parts) > 1 else root_children
            parent.append({
                "name": entry.name, "kind": "file", "path": entry.path,
                "open": False, "current": False, "children": [],
            })

    def sort_children(children: list[dict]) -> None:
        children.sort(
            key=lambda n: (0 if n["kind"] == "dir" else 1, natural_key(n["name"]))
        )
        for child in children:
            sort_children(child["children"])

    sort_children(root_children)

    # 展开当前路径：祖先链 + 当前目录本身；标记当前条目
    if current_path:
        parts = current_path.split("/")
        for i in range(1, len(parts) + (0 if current_path.endswith("/") else 1)):
            node = nodes.get("/".join(parts[:i]))
            if node is not None:
                node["open"] = True
        node = nodes.get(current_path)
        if node is not None:
            node["current"] = True  # 目录：已在 nodes 里
        else:
            _mark_file_current(root_children, current_path)  # 文件：递归查找
    return root_children


def _mark_file_current(children: list[dict], path: str) -> None:
    for child in children:
        if child["path"] == path:
            child["current"] = True
            return
        _mark_file_current(child["children"], path)
