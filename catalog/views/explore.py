"""发现页：分组筛选、关键词搜索、排序与分页。

搜索提交、分页和筛选写入 URL；普通请求渲染完整页面，
普通 HTMX 请求仅替换列表区域；HX-History-Restore-Request
必须返回完整页面，避免历史缓存未命中时恢复成局部列表。
"""
from urllib.parse import urlencode

from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import render

from catalog import selectors

SORT_OPTIONS = {"updated": "最近更新", "name": "名称"}
_PAGE_WINDOW = 2  # 当前页两侧各显示的页数


def _build_url(group_slug, q, sort, page):
    params = {}
    if group_slug:
        params["group"] = group_slug
    if q:
        params["q"] = q
    if sort != "updated":
        params["sort"] = sort
    if page and page != 1:
        params["page"] = page
    return "/explore/" + ("?" + urlencode(params) if params else "")


def _page_links(group_slug, q, sort, page):
    """分页链接：总页数 ≤7 全部显示；否则首末页 + 当前页窗口，间隙用省略号。"""
    num, total = page.number, page.paginator.num_pages
    if total <= 7:
        numbers = list(range(1, total + 1))
    else:
        lo = max(2, num - _PAGE_WINDOW)
        hi = min(total - 1, num + _PAGE_WINDOW)
        numbers = sorted({1, total, *range(lo, hi + 1)})

    entries = []
    if num > 1:
        entries.append(
            {"label": "上一页", "href": _build_url(group_slug, q, sort, num - 1), "kind": "prev"}
        )
    prev = None
    for n in numbers:
        if prev is not None and n - prev > 1:
            entries.append({"label": "…", "kind": "ellipsis"})
        entries.append(
            {
                "label": str(n),
                "href": _build_url(group_slug, q, sort, n),
                "kind": "current" if n == num else "page",
            }
        )
        prev = n
    if num < total:
        entries.append(
            {"label": "下一页", "href": _build_url(group_slug, q, sort, num + 1), "kind": "next"}
        )
    return entries


def explore(request):
    group_slug = request.GET.get("group", "").strip() or None
    q = request.GET.get("q", "").strip()[:100]
    sort = request.GET.get("sort")
    if sort not in SORT_OPTIONS:
        sort = "updated"
    try:
        page_number = max(int(request.GET.get("page", 1) or 1), 1)
    except ValueError:
        page_number = 1

    groups = list(selectors.group_nav())
    current_group = next((g for g in groups if g.slug == group_slug), None)
    active_slug = current_group.slug if current_group else None

    page = selectors.paginate(selectors.project_list(group_slug=active_slug, q=q, sort=sort), page_number)

    context = {
        "nav": "explore",
        "page_title": current_group.name if current_group else "全部项目",
        "groups": groups,
        "current_group": current_group,
        "group_links": [
            {
                "group": g,
                "href": _build_url(g.slug, q, sort, 1),
                "is_active": current_group is not None and g.slug == current_group.slug,
            }
            for g in groups
        ],
        "all_groups_href": _build_url(None, q, sort, 1),
        "total_projects": sum(g.project_count for g in groups),
        "q": q,
        "sort": sort,
        "sort_links": [
            {"key": key, "label": label, "href": _build_url(active_slug, q, key, 1)}
            for key, label in SORT_OPTIONS.items()
        ],
        "page": page,
        "projects": page.object_list,
        "page_links": _page_links(active_slug, q, sort, page),
        "recent": selectors.recent_projects(),
    }

    is_hx = request.headers.get("HX-Request") == "true"
    restore = request.headers.get("HX-History-Restore-Request") == "true"
    template = (
        "catalog/partials/explore_results.html"
        if (is_hx and not restore)
        else "catalog/explore.html"
    )
    response = render(request, template, context)
    response["Vary"] = "HX-Request, HX-History-Restore-Request"
    return response
