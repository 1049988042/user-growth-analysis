-- 用户增长与留存分析
-- 来源：在 MySQL Workbench 中实际使用的 analysis_original.sql 整理版
-- 数据库：MySQL 8.0
--
-- 核心口径：
-- 1. 有效付费订单仅包含“已完成”订单。
-- 2. 次日留存为注册后第 1 天当天登录，7 日留存为注册后第 7 天当天登录。
-- 3. 同一用户同一天多次登录只计为 1 名留存用户。
-- 4. 渠道留存对比统一使用注册日期 <= 2025-09-23 的 cohort。
-- 5. 复购率分母为付费用户，而非全部注册用户。

USE user_analysis;


-- ============================================================
-- 1. 用户增长分析
-- ============================================================

-- 1.1 每日新增用户趋势
SELECT
    注册日期,
    COUNT(*) AS 每日新增用户
FROM users
GROUP BY 注册日期
ORDER BY 注册日期;

-- 1.2 异常注册日期的渠道拆解
-- 实际分析中重点比较了 2025-07-13、2025-07-14 与其他 90 天的渠道日均注册量。
SELECT
    abnormal_days.注册渠道,
    abnormal_days.`7月13日`,
    abnormal_days.`7月14日`,
    normal_days.其他日期日均
FROM (
    SELECT
        注册渠道,
        SUM(CASE WHEN 注册日期 = '2025-07-13' THEN 1 ELSE 0 END) AS `7月13日`,
        SUM(CASE WHEN 注册日期 = '2025-07-14' THEN 1 ELSE 0 END) AS `7月14日`
    FROM users
    GROUP BY 注册渠道
) AS abnormal_days
JOIN (
    SELECT
        注册渠道,
        ROUND(COUNT(*) / 90, 2) AS 其他日期日均
    FROM users
    WHERE 注册日期 NOT IN ('2025-07-13', '2025-07-14')
    GROUP BY 注册渠道
) AS normal_days
    ON abnormal_days.注册渠道 = normal_days.注册渠道
ORDER BY abnormal_days.注册渠道;


-- ============================================================
-- 2. 留存分析
-- ============================================================

-- 2.1 确认登录数据截止时间，用于判断完整观察窗口
SELECT MAX(登录时间) AS 最大登录时间
FROM login;

-- 2.2 整体次日留存率
-- 登录数据截止 2025-09-30，因此仅使用注册日期 <= 2025-09-29 的用户。
SELECT
    COUNT(DISTINCT u.用户ID) AS 注册用户数,
    COUNT(DISTINCT l.用户ID) AS 次日留存用户数,
    ROUND(
        COUNT(DISTINCT l.用户ID) / COUNT(DISTINCT u.用户ID),
        2
    ) AS 次日留存率
FROM users u
LEFT JOIN login l
    ON u.用户ID = l.用户ID
    AND DATEDIFF(l.登录时间, u.注册日期) = 1
WHERE u.注册日期 <= '2025-09-29';

-- 2.3 每日注册 cohort 的次日留存
SELECT
    u.注册日期 AS 日期,
    COUNT(DISTINCT u.用户ID) AS 注册人数,
    COUNT(DISTINCT l.用户ID) AS 次日留存人数,
    ROUND(
        COUNT(DISTINCT l.用户ID) / COUNT(DISTINCT u.用户ID),
        2
    ) AS 次日留存率
FROM users u
LEFT JOIN login l
    ON u.用户ID = l.用户ID
    AND DATEDIFF(l.登录时间, u.注册日期) = 1
WHERE u.注册日期 <= '2025-09-29'
GROUP BY u.注册日期
ORDER BY u.注册日期;

-- 2.4 整体 7 日留存率
-- 登录数据截止 2025-09-30，因此仅使用注册日期 <= 2025-09-23 的用户。
SELECT
    COUNT(DISTINCT u.用户ID) AS 注册用户数,
    COUNT(DISTINCT l.用户ID) AS `7日留存用户数`,
    ROUND(
        COUNT(DISTINCT l.用户ID) / COUNT(DISTINCT u.用户ID),
        2
    ) AS `7日留存率`
FROM users u
LEFT JOIN login l
    ON u.用户ID = l.用户ID
    AND DATEDIFF(l.登录时间, u.注册日期) = 7
WHERE u.注册日期 <= '2025-09-23';

-- 2.5 每日注册 cohort 的 7 日留存
SELECT
    u.注册日期 AS 日期,
    COUNT(DISTINCT u.用户ID) AS 注册人数,
    COUNT(DISTINCT l.用户ID) AS `7日留存人数`,
    ROUND(
        COUNT(DISTINCT l.用户ID) / COUNT(DISTINCT u.用户ID),
        2
    ) AS `7日留存率`
FROM users u
LEFT JOIN login l
    ON u.用户ID = l.用户ID
    AND DATEDIFF(l.登录时间, u.注册日期) = 7
WHERE u.注册日期 <= '2025-09-23'
GROUP BY u.注册日期
ORDER BY u.注册日期;

-- 2.6 不同注册渠道的次日与 7 日留存率
-- 两项指标统一使用注册日期 <= 2025-09-23 的 cohort，保证可以直接比较。
SELECT
    u.注册渠道,
    COUNT(DISTINCT u.用户ID) AS 注册人数,
    COUNT(DISTINCT CASE
        WHEN DATEDIFF(l.登录时间, u.注册日期) = 1 THEN u.用户ID
    END) AS 次日留存人数,
    ROUND(
        COUNT(DISTINCT CASE
            WHEN DATEDIFF(l.登录时间, u.注册日期) = 1 THEN u.用户ID
        END) / COUNT(DISTINCT u.用户ID),
        2
    ) AS 次日留存率,
    COUNT(DISTINCT CASE
        WHEN DATEDIFF(l.登录时间, u.注册日期) = 7 THEN u.用户ID
    END) AS `7日留存人数`,
    ROUND(
        COUNT(DISTINCT CASE
            WHEN DATEDIFF(l.登录时间, u.注册日期) = 7 THEN u.用户ID
        END) / COUNT(DISTINCT u.用户ID),
        2
    ) AS `7日留存率`
FROM users u
LEFT JOIN login l
    ON u.用户ID = l.用户ID
WHERE u.注册日期 <= '2025-09-23'
GROUP BY u.注册渠道
ORDER BY 次日留存率 DESC;


-- ============================================================
-- 3. 付费转化与用户价值分析
-- ============================================================

-- 3.1 检查订单状态及各状态订单量
SELECT
    订单状态,
    COUNT(*) AS 订单数
FROM orders
GROUP BY 订单状态;

-- 3.2 不同注册渠道的累计付费转化与用户价值
-- LEFT JOIN 保留未付费用户作为转化率分母。
-- 只有“已完成”订单计入付费用户、订单数和金额。
SELECT
    u.注册渠道,
    COUNT(DISTINCT u.用户ID) AS 注册用户数,
    COUNT(DISTINCT o.用户ID) AS 付费用户数,
    COUNT(o.订单ID) AS 已完成订单数,
    ROUND(SUM(o.实付金额), 2) AS 总实付金额,
    ROUND(
        SUM(o.实付金额) / COUNT(DISTINCT o.用户ID),
        2
    ) AS 付费用户人均消费,
    ROUND(
        SUM(o.实付金额) / COUNT(o.订单ID),
        2
    ) AS 客单价,
    ROUND(
        COUNT(DISTINCT o.用户ID) / COUNT(DISTINCT u.用户ID),
        2
    ) AS 付费转化率
FROM users u
LEFT JOIN orders o
    ON u.用户ID = o.用户ID
    AND o.订单状态 = '已完成'
GROUP BY u.注册渠道
ORDER BY 付费转化率 DESC, 注册用户数 DESC;


-- ============================================================
-- 4. 复购分析
-- ============================================================

-- 第一层按“用户 + 注册渠道”统计已完成订单数；
-- 第二层按渠道统计至少有 2 笔已完成订单的用户，分母为付费用户数。
SELECT
    user_orders.注册渠道,
    COUNT(*) AS 付费用户数,
    SUM(
        CASE WHEN user_orders.已完成订单数 >= 2 THEN 1 ELSE 0 END
    ) AS 复购用户数,
    ROUND(
        SUM(CASE WHEN user_orders.已完成订单数 >= 2 THEN 1 ELSE 0 END)
        / COUNT(*),
        2
    ) AS 复购率
FROM (
    SELECT
        o.用户ID,
        u.注册渠道,
        COUNT(o.订单ID) AS 已完成订单数
    FROM users u
    JOIN orders o
        ON u.用户ID = o.用户ID
        AND o.订单状态 = '已完成'
    GROUP BY o.用户ID, u.注册渠道
) AS user_orders
GROUP BY user_orders.注册渠道
ORDER BY 复购率 DESC;

