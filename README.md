# wt-035 oneflight 请求合并与并发去重（从 0 实现）

起始环境只有说明与素材：`samples/scripts/*.json` 时序脚本、`samples/expected/*.txt` 逐行期望轨迹；引擎与页面都
要新写，页面不预置文件。

## 1. 范围

做的：读脚本、按第 2 节口径跑确定性模拟、写逐行轨迹（`trace`）；把全部样例的结果内联成一个单文件 HTML
（`report`）；测试用标准库 `unittest`。

不做：结果缓存（只共享正在跑的那次）、自动重试或补发、跨脚本/进程/机器的共享、真实后端与网络与数据库、线程与
异步 IO、读系统时钟或 `sleep`、非法脚本容错（`samples/` 保证合法）、第三方库与 CDN 与构建、页面上的编辑与交互。

## 2. 口径与公式

键 = 脚本里的 `key` 字符串；合并只在同一个键上发生，作用域是「这次 `trace` 里的这一个脚本」，跨脚本不继承状态；
不同键之间只共享 `max_inflight` 一个配额。

在途表 `inflight[key] = e#` 只放正在跑的真执行。请求到达时依次判（三种结局都是终态：不等待、不重发）：

1. 键有在途执行 → 挂上去；已挂着 `max_waiters` 个请求（含发起者）就拒绝 `waiter_limit`；
2. 键没在途执行、在跑的真执行数已到 `max_inflight` → 拒绝 `inflight_limit`；
3. 其余 → 当发起者，真执行开始，`duration`/`result` 取自己那条到达事件。

结果共享：真执行跑完时把 `ok`/`err` 按挂载顺序发给还挂着的每个请求（发起者最前），随后释放键与配额。

失败传播与重发判定：失败不触发自动重发、重试或补偿，等待者各拿一份同样的 `err`；再次真执行只能来自该键在途执行
结束后到达的新请求，哪怕落在失败那一毫秒。成功同样不缓存。

取消与超时：到达时定 `deadline = 到达时刻 + timeout`（没写取 `default_timeout`）；`cancel` 事件记
`reason=user`，到 `deadline` 没结束记 `reason=timeout`，两者都拿不到结果。发起者去留不影响真执行；挂着的请求全
走光时真执行当场 `XCANCEL`（已计入真执行次数，此后不再 `XEND`），键与配额当场释放。

时间与调度：没有线程与 `sleep`，不读系统时钟，逻辑时钟只由脚本事件与 `duration` 推进。同刻优先级固定：到期超时
0 → 脚本取消 1 → 到点完成 2 → 到达 3；所以超时与完成同刻时超时先生效，完成与到达同刻时完成先释放键、到达的请求
另起一次执行。真执行占用左闭右开区间 `[开始, 开始+duration)`。

## 3. 状态机与数据结构

请求 `r#`：键、`deadline`、挂在哪次真执行上；`ARRIVE` 之后、`RETURN`/`CANCEL`/`REJECT` 之前是「挂着」，之后是
终态。

真执行 `e#`（按 `START` 先后编号）：键、挂载顺序的请求列表、开始时刻、结果、状态
`running`/`done`/`cancelled`，只有 `running` 占键与配额。迁移：到达 → 挂载 / 当发起者 / 被拒；跑完 → 逐个
`RETURN` 后 `done`；挂载清零 → `XCANCEL` 后 `cancelled`。

实现要有键、在途执行、请求三张索引，以及按 `(时刻, 优先级)` 取下一事件的调度结构。

## 4. 输入输出与文件格式

### 4.1 时序脚本（`samples/scripts/<名>.json`）

UTF-8、纯 ASCII、无 BOM、单 `\n`。顶层 `name`、`max_waiters`（≥ 1）、`max_inflight`（≥ 1）、
`default_duration`、`default_timeout`、`events`；`events` 按 `t` 非降序：

- 到达 `{"t","arrive","key",…}`：`arrive` 是 `r` + 数字；`duration`、`result`（`ok`/`err`）、`timeout` 可省，
  省略取脚本默认值，且只有它当上发起者时 `duration`/`result` 才起作用。
- 取消 `{"t","cancel","reason":"user"}`：指向当时还挂着的请求。

脚本保证 `duration ≥ 1`、`timeout ≥ 1`、`t` 非降序、请求 id 唯一。

### 4.2 轨迹（`samples/expected/<名>.txt`）

纯 ASCII、单 `\n`、末行也换行；一行一条记录，6 段用 `|` 分隔、空位写 `-`：

```
<时刻>|<事件>|<请求>|<键>|<真执行>|<明细>
```

`<时刻>` 毫秒十进制、不足 4 位左侧补 0；`<明细>` 空写 `-`，否则是空格分隔的 `k=v`：

- `ARRIVE r# 键 - -` 到达，先记后判归属
- `MERGE r# 键 e# pos=<n>` 并入既有真执行，`n` = 追加后的挂载数
- `REJECT r# 键 - reason=waiter_limit|inflight_limit` 到达即被拒
- `START - 键 e# duration=<ms>` 真执行开始
- `XEND - 键 e# result=ok|err` 真执行跑完
- `XCANCEL - 键 e# reason=no_waiters` 真执行中止
- `RETURN r# 键 e# result=ok|err` 结果发给某个请求
- `CANCEL r# 键 e# reason=user|timeout` 请求脱离等待
- `SUMMARY - - -` 末行汇总，字母序 `arrive= cancel= err= exec= merge= ok= reject= return= xcancel=`，`t` 取最后一
  条记录的时刻

### 4.3 命令行

在仓库根目录执行，退出码 0 表示跑完，日志走 stderr：

```
python -m oneflight trace  <脚本> <轨迹文件>
python -m oneflight report <报告文件>
```

`trace` 只跑给的那一个脚本；`report` 按文件名升序读 `samples/scripts/` 全部脚本，结果内联进 HTML。

### 4.4 页面这一侧

`report` 写一个自包含 HTML：数据全部内联，不 `fetch`、不引 CDN、不用起服务，双击（`file://`）即开；同仓库跑两遍
逐字节相同。必须体现：

1. 每条请求一行：请求、键、到达时刻、结束时刻（`RETURN`/`CANCEL`/`REJECT` 那一刻）、结局（4.2 的 result 或
   reason）、并进哪次真执行（被拒写 `-`）。
2. 每次真执行一行：编号、键、开始与结束时刻（`XCANCEL` 写中止时刻）、结局、挂载数与挂载的请求。
3. 汇总：每个脚本一组数字，与 `SUMMARY` 九个口径一一对应，最后一行全样例合计。

## 5. 性能与验收口径

规模：单脚本到达事件 ≤ 5×10^4、键数 ≤ 10^4；`trace` ≤ **2 秒**、`report` ≤ **5 秒**（单进程、含读写）；
`trace` 额外峰值内存 ≤ **64 MiB**（`tracemalloc` 扣基线）。

1. 环境：Python 3.13、只用标准库、无构建无网络；测试用 `unittest`（`python -m unittest discover`），用例只读
   `samples/`。
2. 正确性：六个脚本的 `trace` 与 `samples/expected/<名>.txt` 逐字节相同（含结尾换行）。
3. 确定性：同脚本跑两遍 sha256 相同，`report` 跑两遍逐字节相同。
4. 边界：`timeout` 管同刻优先级，`same_key_concurrent` 管左闭右开，`limits` 管被拒不占配额，`waiter_cancel` 管
   `XCANCEL` 后的当场释放。
5. 页面：双击能开，4.4 三项齐全，数字与 `trace` 一致。
6. 规模：上面两条预算，脚本自备（照 4.1 放大）。

## 6. 样例说明

`samples/scripts/<名>.json` 与 `samples/expected/<名>.txt` 一一对应，括号里是 `SUMMARY` 九个数；`samples/notes.md`
是现场记录，不参与判定：

- `same_key_concurrent`（5/0/0/2/3/2/0/5/0）四个请求共用一份结果；第五个请求在结束的同一毫秒到达，另起一次。
- `exec_failure`（5/0/1/3/2/2/0/5/0）失败按原样发给两个请求、不重发；同毫秒到达另起一次；成功也不复用。
- `waiter_cancel`（5/4/0/2/3/1/0/1/1）等待者与发起者先后取消，最后一个离开时 `XCANCEL`。
- `timeout`（5/4/0/2/3/1/0/1/1）一名等待者超时；发起者 `deadline` 与完成同毫秒、超时先生效；另一键全员超时触发
  `XCANCEL`。
- `limits`（8/0/0/3/2/3/3/5/0）等待人数上限拒 2 次、在跑键数上限拒 1 次；被拒的不占配额，释放后同键照常重来。
- `distinct_keys`（7/0/1/4/3/3/0/7/0）三个键交错、各自合并与结束；一个键结束后再来请求另起执行。

核对：`python -m oneflight trace samples/scripts/limits.json var/limits.txt` 后与期望文件逐字节比。

## 7. 待补的文档

真实规模脚本（仓库只放小样例）、现场参数取值、页面分页、非法脚本的报错与退出码、配色，都没定。
