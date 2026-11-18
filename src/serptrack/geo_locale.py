"""单一职责：地域/语言/设备条件解析与配置回退。

回退后的条件归入 *实际条件*（actual），原始请求条件原样保留（requested），
两者分别落库，历史只按实际条件串联，绝不混进原条件的历史。
"""
from .models import Context, ResolvedContext


def _matches(rule: dict, ctx: Context) -> bool:
    want = rule.get("match", {})
    for key in ("geo", "lang", "device"):
        if key in want and want[key] != getattr(ctx, key):
            return False
    return True


def resolve(ctx: Context, fallback_rules: list[dict]) -> ResolvedContext:
    """按顺序应用第一条匹配的回退规则；无匹配则实际条件等于请求条件。"""
    for rule in fallback_rules or []:
        if _matches(rule, ctx):
            actual_payload = rule.get("actual", {})
            actual = Context(
                geo=actual_payload.get("geo", ctx.geo),
                lang=actual_payload.get("lang", ctx.lang),
                device=actual_payload.get("device", ctx.device),
            )
            return ResolvedContext(
                requested=Context(**ctx.to_dict()),
                actual=actual,
                fallback_applied=True,
                reason=str(rule.get("reason", "fallback")),
            )
    return ResolvedContext(
        requested=Context(**ctx.to_dict()),
        actual=Context(**ctx.to_dict()),
        fallback_applied=False,
        reason="",
    )


def context_label(ctx: Context) -> str:
    return f"{ctx.geo}/{ctx.lang}/{ctx.device}"
