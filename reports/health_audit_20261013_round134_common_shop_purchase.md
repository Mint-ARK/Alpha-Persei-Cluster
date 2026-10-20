# 第134轮｜常用商店购买、目录归属与限购刷新

扫描日期：2026-10-13。问题编号：A275、A276。

## A275｜商店未核对货架与店铺归属，未上架或跨店商品也能买

### 出现什么现象

未上架商品或错误店铺ID也能购买，收据和广播随请求写错归属。

### 初步定位

_find_goods_cfg虽然接收shop_id，却按goods_id全局查找并遍历候选池，没有核用户当前货架与合法店铺；角色碎片等商品还缺持有门槛。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `shop_service.py` | _find_goods_cfg、buy_goods与store_purchase |

## A276｜永久限购按一天后过期处理，旧商品可再次兑换

### 出现什么现象

永久限购商品过24小时后又能购买。

### 初步定位

refresh_cycle1被通用分支换算成一天期限，读购买次数和TIME_TICK都按到期删除收据，与同模块永久限购语义冲突。

### 相关文件与入口

| 文件 | 定位入口 |
| --- | --- |
| `shop_service.py` | refresh_cycle、_get_bought、on_time_tick |

## 关联问题

| 编号 | 问题 |
| --- | --- |
| A01 | [商店按客户端上报金额扣费，可能绕过配置价格](<health_audit_20260920.md>) |
| A242 | [周期礼包扣费成功但订阅或首日邮件缺失](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A243 | [周期礼包补发后进度保存失败，重试重复寄出邮件](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A244 | [周期礼包次日邮件已生成，在线未读和红点未刷新](<health_audit_20261010_round126_periodic_gift_lifecycle.md>) |
| A274 | [累充和战令装扮被当普通材料发放，名片里无法使用](<health_audit_20261013_round133_recharge_special_assets.md>) |
