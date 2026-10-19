"""用户向量、生图连接及不同向量空间隔离。"""
from alembic import op
import sqlalchemy as sa

revision = 'f96db2ef345a'
down_revision = 'f85ca1de234f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_resource_models',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        *[sa.Column(name, sa.String(length), nullable=False) for name, length in (
            ('kind', 20), ('name', 100), ('provider', 100), ('model', 100), ('protocol', 30), ('base_url', 2048))],
        sa.Column('encrypted_api_key', sa.Text(), nullable=False),
        sa.Column('dimensions', sa.Integer(), nullable=True),
        sa.Column('send_dimensions', sa.Boolean(), nullable=False),
        sa.Column('image_sizes', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('user_id', 'kind', 'name', name='uq_resource_models_owner_kind_name'),
        sa.CheckConstraint("kind IN ('embedding','image')", name='ck_resource_model_kind'))
    op.create_index('ix_user_resource_models_user_id', 'user_resource_models', ['user_id'])
    op.create_index('uq_resource_models_default', 'user_resource_models', ['user_id', 'kind'],
                    unique=True, postgresql_where=sa.text('is_default'))
    op.add_column('document_chunks', sa.Column('embedding_space', sa.String(64), nullable=False, server_default='legacy'))
    op.drop_constraint('uq_document_chunks_position', 'document_chunks', type_='unique')
    op.create_unique_constraint('uq_document_chunks_position', 'document_chunks',
                               ['document_id', 'embedding_space', 'embedding_model', 'embedding_dimensions', 'chunk_index'])


def downgrade():
    # 不同空间可能存在同一片段，先确认没有重复后才能安全恢复旧约束。
    op.drop_constraint('uq_document_chunks_position', 'document_chunks', type_='unique')
    op.create_unique_constraint('uq_document_chunks_position', 'document_chunks',
                               ['document_id', 'embedding_model', 'embedding_dimensions', 'chunk_index'])
    op.drop_column('document_chunks', 'embedding_space')
    op.drop_table('user_resource_models')
