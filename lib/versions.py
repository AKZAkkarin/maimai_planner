# 用途：版本主表 / 别名 / DX rating 可用版本（server、fusion、analysis、dxrating 共用）
# 状态：现行（2026-10-24 统一主表）

# 历史脏名 / 本地原始名 → 规范名。
# 只放“确实存在”的别名；未知值由 canonical_version 原样返回。
VERSION_ALIASES = {
    "maimaiでらっくす": "DX",
    "maimaiでらっくす PLUS": "DX PLUS",
    "maimaiでらっくすPLUS": "DX PLUS",
    "maimaDX": "DX",
    "maimaDXPLUS": "DX PLUS",
    "maimai DX": "DX",
    "maimai DX PLUS": "DX PLUS",
    "CiRCLEPLUS": "CiRCLE PLUS",
}
# 兼容旧名字：调用方一直用 VERSION_FIX.get(...)
VERSION_FIX = VERSION_ALIASES

# 版本先后（旧 → 新）；b50 的「新曲池」= 最新两个版本
VERSION_ORDER = (
    "maimai", "maimai PLUS", "GreeN", "GreeN PLUS", "ORANGE", "ORANGE PLUS",
    "PiNK", "PiNK PLUS", "MURASAKi", "MURASAKi PLUS", "MiLK", "MiLK PLUS", "FiNALE",
    "DX", "DX PLUS", "Splash", "Splash PLUS", "UNiVERSE", "UNiVERSE PLUS",
    "FESTiVAL", "FESTiVAL PLUS", "BUDDiES", "BUDDiES PLUS",
    "PRiSM", "PRiSM PLUS", "CiRCLE", "CiRCLE PLUS", "MAGiCAL",
)
NEW_POOL_SIZE = 2
# 从这一版起 B15 计“本版本 + 上一版本”；更早只算本版本。
B15_DOUBLE_FROM = "CiRCLE"
# 可选“当前版本”的范围：旧框不能作为当前版本；B35 仍可包含旧框旧曲。
RATING_VERSIONS = VERSION_ORDER[VERSION_ORDER.index("DX"):]


def canonical_version(value: str | None) -> str:
    """别名/脏名 → 规范名；未知值去空白后原样返回（None → ""）。"""
    if value is None:
        return ""
    name = str(value).strip()
    return VERSION_ALIASES.get(name, name)


def fix_version(value: str | None) -> str:
    """兼容旧调用名；等价于 canonical_version。"""
    return canonical_version(value)


def new_song_versions(versions) -> tuple[str, set[str]]:
    """按库里出现过的版本推出当前版本，返回 (当前版本, 新曲池版本集合)。"""
    present_names = {canonical_version(v) for v in versions}
    present = [v for v in VERSION_ORDER if v in present_names]
    if not present:
        return "", set()
    current = present[-1]
    idx = VERSION_ORDER.index(current)
    return current, set(VERSION_ORDER[max(0, idx - NEW_POOL_SIZE + 1):idx + 1])
