"""用户增长与留存分析。

默认从项目内的 Excel 模拟数据读取并生成四张核心图表；也可以通过
``--source mysql`` 从本地 MySQL 读取同名数据表。数据库凭据仅从环境变量读取。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "data" / "user_growth_data.xlsx"
IMAGE_DIR = PROJECT_ROOT / "images"
TABLE_NAMES = ("users", "login", "orders", "products")
COMPLETED_STATUS = "已完成"
RETENTION_COHORT_END = pd.Timestamp("2025-09-23")

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


def load_from_excel(path: Path = DATA_FILE) -> dict[str, pd.DataFrame]:
    """读取项目内的四张业务数据表。"""
    return {name: pd.read_excel(path, sheet_name=name) for name in TABLE_NAMES}


def build_mysql_engine():
    """根据环境变量创建数据库连接，避免在代码中保存密码。"""
    try:
        from sqlalchemy import URL, create_engine
    except ImportError as exc:
        raise RuntimeError("使用 MySQL 数据源前请安装 SQLAlchemy 和 PyMySQL。") from exc

    required = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"缺少数据库环境变量：{', '.join(missing)}")

    url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["DB_NAME"],
    )
    return create_engine(url)


def load_from_mysql() -> dict[str, pd.DataFrame]:
    """从 MySQL 读取数据表；表结构应与 Excel 工作表保持一致。"""
    engine = build_mysql_engine()
    try:
        return {name: pd.read_sql_table(name, engine) for name in TABLE_NAMES}
    finally:
        engine.dispose()


def prepare_dates(tables: dict[str, pd.DataFrame]) -> None:
    """统一日期字段类型。"""
    tables["users"]["注册日期"] = pd.to_datetime(tables["users"]["注册日期"])
    tables["login"]["登录时间"] = pd.to_datetime(tables["login"]["登录时间"])
    tables["orders"]["下单时间"] = pd.to_datetime(tables["orders"]["下单时间"])


def run_quality_checks(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """执行缺失、重复、主键、外键和时间逻辑检查。"""
    users, login = tables["users"], tables["login"]
    orders, products = tables["orders"], tables["products"]

    checks = {
        "全部表缺失值数": sum(df.isna().sum().sum() for df in tables.values()),
        "全部表完全重复行数": sum(df.duplicated().sum() for df in tables.values()),
        "users 主键重复数": users["用户ID"].duplicated().sum(),
        "login 主键重复数": login["登录ID"].duplicated().sum(),
        "orders 主键重复数": orders["订单ID"].duplicated().sum(),
        "products 主键重复数": products["商品ID"].duplicated().sum(),
        "orders 无效用户ID数": (~orders["用户ID"].isin(users["用户ID"])).sum(),
        "login 无效用户ID数": (~login["用户ID"].isin(users["用户ID"])).sum(),
        "orders 无效商品ID数": (~orders["商品ID"].isin(products["商品ID"])).sum(),
    }

    order_dates = orders[["用户ID", "下单时间"]].merge(
        users[["用户ID", "注册日期"]], on="用户ID", how="left"
    )
    login_dates = login[["用户ID", "登录时间"]].merge(
        users[["用户ID", "注册日期"]], on="用户ID", how="left"
    )
    checks["注册前下单记录数"] = (order_dates["下单时间"] < order_dates["注册日期"]).sum()
    checks["注册前登录记录数"] = (login_dates["登录时间"] < login_dates["注册日期"]).sum()

    result = pd.DataFrame({"检查项": checks.keys(), "异常记录数": checks.values()})
    if result["异常记录数"].sum() != 0:
        raise ValueError("数据质量检查发现异常，请先查看检查结果。")
    return result


def analyze_growth(users: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算每日新增用户与渠道注册规模。"""
    daily = users.groupby("注册日期", as_index=False).agg(新增注册人数=("用户ID", "nunique"))
    channels = (
        users.groupby("注册渠道", as_index=False)
        .agg(注册用户数=("用户ID", "nunique"))
        .sort_values("注册用户数", ascending=False)
    )
    return daily, channels


def analyze_retention(users: pd.DataFrame, login: pd.DataFrame) -> pd.DataFrame:
    """在统一 cohort 中计算注册后第 1 天和第 7 天当天留存率。"""
    cohort = users.loc[
        users["注册日期"] <= RETENTION_COHORT_END,
        ["用户ID", "注册日期", "注册渠道"],
    ]
    joined = cohort.merge(login[["用户ID", "登录时间"]], on="用户ID", how="left")
    joined["注册后天数"] = (
        joined["登录时间"].dt.normalize() - joined["注册日期"].dt.normalize()
    ).dt.days

    base = cohort.groupby("注册渠道")["用户ID"].nunique().rename("注册用户数")
    day_1 = (
        joined.loc[joined["注册后天数"] == 1]
        .groupby("注册渠道")["用户ID"]
        .nunique()
        .rename("次日留存用户数")
    )
    day_7 = (
        joined.loc[joined["注册后天数"] == 7]
        .groupby("注册渠道")["用户ID"]
        .nunique()
        .rename("7日留存用户数")
    )
    result = pd.concat([base, day_1, day_7], axis=1).fillna(0).reset_index()
    result["次日留存率"] = result["次日留存用户数"] / result["注册用户数"]
    result["7日留存率"] = result["7日留存用户数"] / result["注册用户数"]
    return result.sort_values("次日留存率", ascending=False)


def analyze_conversion_and_value(
    users: pd.DataFrame, orders: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """计算累计付费转化、用户价值与复购；只使用已完成订单。"""
    completed = orders.loc[orders["订单状态"] == COMPLETED_STATUS].copy()
    paid_users = completed[["用户ID"]].drop_duplicates().assign(是否付费=1)

    conversion = users[["用户ID", "注册渠道"]].merge(paid_users, on="用户ID", how="left")
    conversion["是否付费"] = conversion["是否付费"].fillna(0)
    conversion = conversion.groupby("注册渠道", as_index=False).agg(
        注册用户数=("用户ID", "nunique"), 付费用户数=("是否付费", "sum")
    )
    conversion["付费用户数"] = conversion["付费用户数"].astype(int)
    conversion["付费转化率"] = conversion["付费用户数"] / conversion["注册用户数"]
    conversion = conversion.sort_values("付费转化率", ascending=False)

    paid_orders = completed.merge(users[["用户ID", "注册渠道"]], on="用户ID", how="left")
    value = paid_orders.groupby("注册渠道", as_index=False).agg(
        付费用户数=("用户ID", "nunique"),
        已完成订单数=("订单ID", "nunique"),
        总实付金额=("实付金额", "sum"),
    )
    value["付费用户人均消费"] = value["总实付金额"] / value["付费用户数"]
    value["客单价"] = value["总实付金额"] / value["已完成订单数"]
    value = value.merge(
        conversion[["注册渠道", "注册用户数", "付费转化率"]], on="注册渠道", how="left"
    ).sort_values("付费用户人均消费", ascending=False)

    user_order_counts = paid_orders.groupby(["注册渠道", "用户ID"], as_index=False).agg(
        已完成订单数=("订单ID", "nunique")
    )
    repeat = user_order_counts.groupby("注册渠道", as_index=False).agg(
        付费用户数=("用户ID", "nunique"),
        复购用户数=("已完成订单数", lambda values: (values >= 2).sum()),
    )
    repeat["复购率"] = repeat["复购用户数"] / repeat["付费用户数"]
    repeat = repeat.sort_values("复购率", ascending=False)
    return conversion, value, repeat


def save_charts(
    daily: pd.DataFrame,
    retention: pd.DataFrame,
    conversion: pd.DataFrame,
    value: pd.DataFrame,
) -> None:
    """生成作品集中的四张核心图表。"""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(daily["注册日期"], daily["新增注册人数"])
    ax.set(title="每日新增用户趋势", xlabel="日期", ylabel="新增注册人数")
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    fig.savefig(IMAGE_DIR / "daily_users.png", dpi=150)
    plt.close(fig)

    retention_plot = retention.set_index("注册渠道")[["次日留存率", "7日留存率"]]
    ax = retention_plot.plot(kind="bar", figsize=(8, 5))
    ax.set(title="不同注册渠道的次日与 7 日留存率", xlabel="注册渠道", ylabel="留存率")
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
    ax.tick_params(axis="x", rotation=0)
    ax.figure.tight_layout()
    ax.figure.savefig(IMAGE_DIR / "retention.png", dpi=150)
    plt.close(ax.figure)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(conversion["注册渠道"], conversion["付费转化率"])
    ax.set(title="不同注册渠道的累计付费转化率", xlabel="注册渠道", ylabel="付费转化率")
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
    fig.tight_layout()
    fig.savefig(IMAGE_DIR / "conversion.png", dpi=150)
    plt.close(fig)

    value_plot = value.set_index("注册渠道")[["付费用户人均消费", "客单价"]]
    ax = value_plot.plot(kind="bar", figsize=(8, 5))
    ax.set(title="不同渠道用户价值对比", xlabel="注册渠道", ylabel="金额")
    ax.tick_params(axis="x", rotation=0)
    ax.figure.tight_layout()
    ax.figure.savefig(IMAGE_DIR / "user_value.png", dpi=150)
    plt.close(ax.figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="用户增长与留存分析")
    parser.add_argument("--source", choices=("excel", "mysql"), default="excel")
    args = parser.parse_args()

    tables = load_from_excel() if args.source == "excel" else load_from_mysql()
    prepare_dates(tables)

    quality = run_quality_checks(tables)
    daily, channels = analyze_growth(tables["users"])
    retention = analyze_retention(tables["users"], tables["login"])
    conversion, value, repeat = analyze_conversion_and_value(tables["users"], tables["orders"])
    save_charts(daily, retention, conversion, value)

    print("\n数据质量检查\n", quality.to_string(index=False))
    print("\n渠道注册规模\n", channels.to_string(index=False))
    print("\n统一 cohort 渠道留存\n", retention.round(4).to_string(index=False))
    print("\n渠道累计付费转化\n", conversion.round(4).to_string(index=False))
    print("\n渠道用户价值\n", value.round(2).to_string(index=False))
    print("\n渠道复购\n", repeat.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
