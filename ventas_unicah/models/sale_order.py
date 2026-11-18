from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .res_partner import CANALES_VENTA

MONTO_APROBACION = 50000


class SaleOrder(models.Model):
    _inherit = "sale.order"

    canal_venta = fields.Selection(
        CANALES_VENTA,
        string="Canal de venta",
        compute="_compute_canal_venta",
        store=True,
        readonly=False,
    )
    total_unidades = fields.Float(
        string="Total de unidades",
        compute="_compute_total_unidades",
        store=True,
    )
    requiere_aprobacion = fields.Boolean(
        string="Requiere aprobación",
        compute="_compute_requiere_aprobacion",
        store=True,
    )
    aprobado_por = fields.Many2one(
        "res.users",
        string="Aprobado por",
        readonly=True,
        copy=False,
    )
    pendiente_aprobacion = fields.Boolean(
        compute="_compute_pendiente_aprobacion",
    )

    @api.depends("partner_id")
    def _compute_canal_venta(self):
        for order in self:
            order.canal_venta = order.partner_id.canal_preferido

    @api.depends("order_line.product_uom_qty")
    def _compute_total_unidades(self):
        for order in self:
            order.total_unidades = sum(order.order_line.mapped("product_uom_qty"))

    @api.depends("amount_total")
    def _compute_requiere_aprobacion(self):
        for order in self:
            order.requiere_aprobacion = order.amount_total > MONTO_APROBACION

    @api.depends("requiere_aprobacion", "aprobado_por", "state")
    def _compute_pendiente_aprobacion(self):
        for order in self:
            order.pendiente_aprobacion = (
                order.requiere_aprobacion
                and not order.aprobado_por
                and order.state in ("draft", "sent")
            )

    def write(self, vals):
        """Reto 1: si el total del pedido cambia, la aprobación se anula."""
        recalcular = "amount_total" in vals or "order_line" in vals
        totales_antes = (
            {o.id: o.amount_total for o in self} if recalcular else None
        )
        res = super().write(vals)
        if totales_antes is not None:
            self._anular_aprobacion_si_cambio_total(totales_antes)
        return res

    def _anular_aprobacion_si_cambio_total(self, totales_antes=None):
        if totales_antes is None:
            totales_antes = {}
        cambiados = self.filtered(
            lambda o: o.aprobado_por
            and o.amount_total != totales_antes.get(o.id)
        )
        if cambiados:
            cambiados.write({"aprobado_por": False})
        return cambiados

    @api.constrains("canal_venta", "partner_id")
    def _check_canal_distribuidor(self):
        """Reto 2: el canal Distribuidor solo se permite a clientes empresa."""
        for order in self:
            if (
                order.canal_venta == "distribuidor"
                and order.partner_id
                and not order.partner_id.is_company
            ):
                raise ValidationError(
                    f"El canal Distribuidor solo se permite para clientes "
                    f"empresa. {order.partner_id.name} no es una empresa."
                )


    def action_aprobar(self):
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise AccessError("Solo un administrador de ventas puede aprobar pedidos.")
        self.write({"aprobado_por": self.env.user.id})
        return True

    def action_confirm(self):
        for order in self:
            if order.requiere_aprobacion and not order.aprobado_por:
                raise UserError(
                    f"El pedido {order.name} supera {MONTO_APROBACION:,} y debe ser "
                    "aprobado por un administrador de ventas antes de confirmarse."
                )
        return super().action_confirm()


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def write(self, vals):
        """Reto 1 (parte 2): editar una línea también puede cambiar el total."""
        totales_antes = {l.order_id.id: l.order_id.amount_total for l in self}
        res = super().write(vals)
        self.order_id._anular_aprobacion_si_cambio_total(totales_antes)
        return res
