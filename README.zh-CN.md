# RelayCopy：透明的 SSH/rsync 中转复制

命令叫 **`relaycp`**。只在本地安装，首次通过现有 SSH 自动向源机器投递小型
Python zipapp；文件从源机器直接上传到中转机，多个文件之间可以流水传输。
不在 A5 上安装 Python 包，不经过堡垒机传文件内容，不新增服务或公网端口。

```sh
python3 scripts/build.py
./dist/relaycp.pyz --config /自己的/config.json doctor
./dist/relaycp.pyz --config /自己的/config.json compute:/data/result.tar.gz ./
```

底层复用 OpenSSH、rsync 和 rrsync。项目只封装工具投递、启动、状态与完整性验收。
客户端和源机器要求 Linux、Python 3.10+、OpenSSH 和 rsync 3.2.3+。
中转机只需已有 sshd、rsync、rrsync。

## 一次配置后使用

1. 管理员按 [中转设置](server/README.md) 建立独立接收目录、受限只写上传 key
   和只读下载 key。每个同事/团队使用自己的凭据与目录，不分发主机管理 key。
2. 本地配置 `config.example.json` 中的中转 SSH 别名、来源机器 SSH 别名和 namespace。
3. 可以配置本地 `upload_key_file` 和已认证的 `upload_known_hosts_file`，首次自动
   通过 SSH stdin 投递到源机器，0600 保存；仅能使用已被服务端限制的专用上传 key。
4. 如果 A5 已经有受限上传 key，省略上面两个字段，在 `upload` 中填源机器上现有
   `key_file`、`known_hosts`，直接复用，无需复制私钥。

源端 `upload.host` 应填写源机器实际可访问的地址；本地的 SSH 别名不一定在源端存在。
同事只需获得文件权限，不需要 guan 的 shell 权限。Skill 不承担权限隔离。

## 操作与交接

```sh
# 一条命令；源文件必须已经写完
relaycp compute:/data/result.tar.gz ./

# 路径有空格时正常引用；本地可以改名
relaycp 'compute:/data/result with spaces.tar.gz' ./renamed.tar.gz

# A 上传完成后下载 A，同时上传 B
relaycp compute:/data/A.tar.gz compute:/data/B.tar.gz ./results/

# 使用首次输出的 ID 恢复同一个任务
relaycp compute:/data/result.tar.gz ./ --id rc-首次的ID

# 把 ticket 交给其他 agent 或其他接收机器
relaycp status relay://compute/rc-首次的ID
relaycp receive relay://compute/rc-首次的ID ./
```

stdout 固定输出 JSON Lines，供 agent 解析。首次输出 ticket、resume_id，随后给出
源端 PID/start_ticks、工具 SHA 和日志位置。工具传完即断开控制 SSH；上传后台运行。
中断本地下载不会自动停止源端上传。

接收方先得到 `.partial.relaycp`，整文件大小与 SHA256 验证通过后，才以不覆盖的方式
发布正式文件，并写入 `.relaycp-verified.json`。保留 partial、sidecar 和状态目录可续传。
已有目标文件一致则跳过，不一致则拒绝覆盖。

## 实现边界

这是 **scp 风格的一部分语法**，不是原生 SCP/SFTP 协议，也不替换系统 scp。
v0.1 支持同一远程机器的一个或多个普通文件到本地；递归、glob、一条命令反向上传和远程对远程尚未实现。
可用 `send`/`receive` 在具备相应受限凭据的机器之间显式交接。

仅做文件级流水：A 上传完成后下载 A，同时源端上传 B。单文件不边传边取、不反复读取
增长文件；下载使用一次可续传的 rsync，最终核对 SHA256。等待期间只查小型完成回执。
速度收益取决于两段链路和磁盘；中转机仍需容纳这批完整文件。
不自动清理源文件或中转数据，不提供配额系统；由管理员按存储容量配置隔离和保留策略。

分发 `skills/relaycopy/` 给其他 agent；支持 Skill 的 harness 安装到其约定位置，
其他能执行命令的 agent 可以直接读取同一份 SKILL.md。详细配置、测试与限制见
[English README](README.md)。
