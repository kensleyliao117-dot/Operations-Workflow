"""业务层数据库操作，接收 Session 和 Schema，返回 ORM 对象。

写操作成功后提交，失败后回滚；Session 生命周期交给请求依赖管理。
"""
