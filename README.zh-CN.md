# RelayCopy：让 agent 透明地中转复制文件

命令叫 **`relaycp`**。只在本地安装，复用已有 SSH 别名连接源机器，自动投递小工具和
专用只写上传凭据；数据从源机器经中转机到本地，不通过堡垒机传文件内容。
底层复用 OpenSSH、rsync、rrsync、flock，不自研传输协议或新增网络监听。

## 一次部署，以后只留本地配置

```sh
git clone https://github.com/colorful-lollipop/relaycopy.git
cd relaycopy
python3 scripts/build.py

# my-relay-admin 是你已有且已验证主机身份的管理员 SSH 别名
./dist/relaycp.pyz setup-relay --ssh my-relay-admin --install-deps --apply

# 此后直接使用已有的源机器 SSH 别名，无需逐台填写上传配置
./dist/relaycp.pyz compute:/data/A.tar.gz compute:/data/B.tar.gz ./results/
```

管理员登录非root时可加 `--sudo`，需要免交互sudo。省略 `--apply` 可查看计划。
中转部署要求 Linux、Python 3.10+、systemd；自动安装依赖支持 Debian/Ubuntu。
本地和源机器要求 Python 3.10+、OpenSSH、rsync 3.2.3+；不会自动在源机器安装包。

部署创建独立账号、数据目录、受限凭据和回收定时任务，不改全局sshd配置、不覆盖
其他账号authorized_keys、不增加公网端口。默认配置保存在
`~/.config/relaycopy/config.json`，三把私钥在同一私有目录中以0600保存。
部署工具拒绝把配置和私钥写到任何Git工作区内。

上传、下载、维护使用不同密钥。只有受限只写上传key会下发到源机器；本机管理SSH身份
不下发、不写进源码或Skill。不同同事/团队应分别部署账号、目录和凭据。

## 文件级流水与空间回收

- A上传完成后才下载A，同时源端继续上传B。
- 本地大小和SHA256通过、正式文件发布成功后，立即释放A的中转副本。
- 遗留任务默认保留24小时，systemd每15分钟检查；拿不到独占锁就跳过，保护活跃rsync。
- 原始文件不删除；失败partial保留供续传。清理失败会报错并保留待清理回执。

维护key只能访问固定namespace下的固定维护操作，不能执行shell、端口转发或指定任意路径。
任务ID、完成回执及SHA必须匹配才能释放对应文件。不会扫描或清理其他用户目录。

默认是**单次接收后释放**。需要同一份文件给多个接收方时，把配置里的
`cleanup_after_receive` 设为false，由保留期统一回收；ticket本身不授予访问权限。
这不是硬磁盘配额：活跃的大批量传输仍可能占满盘，建议配独立文件系统或OS配额。

```sh
relaycp gc --dry-run
relaycp gc
```

## 恢复与交接

```sh
relaycp compute:/data/A.tar.gz ./ --id rc-首次的ID
relaycp status relay://files/rc-首次的ID
relaycp receive relay://files/rc-首次的ID ./
```

stdout是JSON Lines。`verified`表示单文件本地校验完成，`batch_verified`表示整批完成，
`relay_released`表示中转副本已释放。保存首次输出的ID、ticket和源端进程回执。
中断本地进程不一定停止远端上传；恢复时保留partial、sidecar和状态目录。
同一ID不用于不同内容。已释放或过期的文件复制到新目的地时，应创建新任务。

当前支持同一远程机器的多个普通文件；可用 `send` / `receive` 显式交接。
这是scp风格语法，不是原生SCP/SFTP协议；目录递归、glob和一键反向复制尚未实现。

## 交给 agent

把 `skills/relaycopy/` 交给agent，并告诉它已有管理员SSH别名，例如：

> 使用 relaycopy Skill，通过 my-relay-admin 部署中转，把私有配置留在本地，然后把
> compute:/data/A.tar.gz 下载到当前目录。

支持Skill的harness按其位置安装；其他能执行命令的agent可直接读取同一SKILL.md。
后续只需让agent使用已有配置，不需要再次部署中转。

详细参数、测试、MIT许可证和安全边界见 [English README](README.md)、
[部署说明](server/README.md) 和 [安全说明](SECURITY.md)。
