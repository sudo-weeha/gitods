"""管理表单：与公共服务共用校验规则，URL 标识创建后固定。

输入错误在表单层给出可见提示；服务层保留同样的约束作为兜底。
"""
from django import forms

from catalog.models import Group, Project
from catalog.services.projects import slug_problem


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "slug", "description", "icon", "sort_order"]

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if not self.instance._state.adding and slug != self.instance.slug:
            raise forms.ValidationError("URL 标识创建后不可修改")
        problem = slug_problem(slug)
        if problem:
            raise forms.ValidationError(problem)
        return slug

    def clean(self):
        cleaned = super().clean()
        if self.instance._state.adding:
            name = cleaned.get("name")
            slug = cleaned.get("slug")
            if name and Group.objects.filter(name=name).exists():
                self.add_error("name", f"分组名称 “{name}” 已存在")
            if slug and Group.objects.filter(slug=slug).exists():
                self.add_error("slug", f"URL 标识 “{slug}” 已被占用")
        return cleaned


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["group", "name", "slug", "description", "language"]

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if not self.instance._state.adding and slug != self.instance.slug:
            raise forms.ValidationError("URL 标识创建后不可修改")
        problem = slug_problem(slug)
        if problem:
            raise forms.ValidationError(problem)
        return slug

    def clean(self):
        cleaned = super().clean()
        if self.instance._state.adding:
            group = cleaned.get("group")
            slug = cleaned.get("slug")
            if group and slug and Project.objects.filter(group=group, slug=slug).exists():
                self.add_error("slug", f"该分组下已存在 URL 标识 “{slug}”")
        else:
            # 编辑页 slug 只读不在表单里，用实例值校验“移动分组”冲突
            group = cleaned.get("group")
            if (
                group is not None
                and group.pk != self.instance.group_id
                and Project.objects.filter(group=group, slug=self.instance.slug)
                .exclude(pk=self.instance.pk)
                .exists()
            ):
                self.add_error("group", f"目标分组下已存在 URL 标识 “{self.instance.slug}”")
        return cleaned


class BranchCreateForm(forms.Form):
    """创建分支：空分支或复制现有分支（服务层校验归属与并发）。"""

    name = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={"size": 30, "placeholder": "例如 release-1.0"}),
    )
    source_branch = forms.UUIDField(required=False)
    expected_revision = forms.IntegerField(required=False, min_value=1)

    def clean_name(self):
        from catalog.services.branches import branch_name_problem

        name = self.cleaned_data["name"]
        problem = branch_name_problem(name)
        if problem:
            raise forms.ValidationError(problem)
        return name
