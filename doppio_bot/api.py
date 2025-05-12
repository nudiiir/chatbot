import frappe
from langchain_google_genai import ChatGoogleGenerativeAI # Changed from langchain.llms import OpenAI
from langchain.memory import RedisChatMessageHistory, ConversationBufferMemory
from langchain.prompts import PromptTemplate
from langchain.agents import tool, AgentType, initialize_agent
from datetime import date
from pydantic import BaseModel, model_validator
from langchain.schema import SystemMessage
from langdetect import detect, DetectorFactory
from frappe import log_error 
from typing import Optional, Dict
from googletrans import Translator
from frappe import get_all, db, utils
from datetime import datetime, timedelta
import logging # Was imported twice, removed one
import calendar
# import os # No longer needed for OPENAI_API_KEY
import json # Added for create_sales_invoice parsing

# Asegurar resultados consistentes en la detección de idioma
DetectorFactory.seed = 0

# Prompt personalizado con instrucción de idioma reforzada
prompt_template = PromptTemplate(
    input_variables=["chat_history", "input"],
    template="""
    Eres un asistente virtual que responde **exclusivamente en español**. 
    No importa el idioma en el que te hablen, siempre debes responder en español.
    Tu tarea es ayudar al usuario de manera clara y precisa, utilizando únicamente el idioma español.

    Historial de la conversación:
    {chat_history}

    Human: {input}
    AI:""",  # El modelo debe responder aquí en español
    template_format="f-string",
)

def is_erpnext_related(prompt_message: str) -> bool:
    """
    Valida si la pregunta está relacionada con ERPNext.
    Puedes usar una lista de palabras clave o un modelo de clasificación simple.
    """
    erpnext_keywords = [
        "erpnext", "cliente", "factura", "venta", "compra", "inventario", 
        "proveedor", "artículo", "pedido", "cotización", "transacción","hola",
        "rotacion","inventario","ultima","informacion","costo","precio","ultimo","alto","ayuda",
        "erp","sistema","datos maestros","producto","item","nit","cui"
    ]
    
    prompt_message = prompt_message.lower()
    return any(keyword in prompt_message for keyword in erpnext_keywords)

@frappe.whitelist()
def get_chatbot_response(session_id: str, prompt_message: str) -> str:
    google_api_key = frappe.conf.get("google_api_key") or frappe.get_site_config().get("google_api_key")
    # os.environ["OPENAI_API_KEY"] = openai_api_key  # Removed

    google_model_name = get_model_from_settings() # Changed from openai_model

    if not google_api_key:
        frappe.throw("Please set `google_api_key` in site config") # Changed from openai_api_key

    if not is_erpnext_related(prompt_message):
        return "Lo siento, solo puedo responder preguntas relacionadas con ERPNext. ¿En qué más puedo ayudarte?"
    
    # Configuración del modelo LLM
    llm = ChatGoogleGenerativeAI(model=google_model_name, google_api_key=google_api_key, temperature=0, convert_system_message_to_human=True) # Changed LLM

    redis_url = frappe.conf.get("redis_cache", "redis://localhost:6379/0")
    message_history = RedisChatMessageHistory(session_id=session_id, url=redis_url)

    memory = ConversationBufferMemory(memory_key="chat_history", chat_memory=message_history)

    tools = [update_customers, create_customer, delete_customers, get_info_customer,
             create_sales_invoice,create_sales_order, get_sales_stats, create_purchase_invoice, create_suppliers,
             get_item_stats,get_sales_stats,create_item,consultar_identificacion_sat]

    system_message = SystemMessage(content="Eres un asistente virtual que responde exclusivamente en español. No importa el idioma en el que te hablen, siempre debes responder en español.")

    agent_chain = initialize_agent(
        tools=tools,
        llm=llm,
        agent=AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
        verbose=True,
        memory=memory,
        handle_parsing_errors = True,
        agent_kwargs={
            "system_message": system_message.content # Pass system message content directly if ChatGoogleGenerativeAI expects it this way
        }
    )

    chat_history_str = memory.load_memory_variables({})["chat_history"]

    response = agent_chain.run({"chat_history": chat_history_str, "input": prompt_message})

    response = ensure_spanish(response)
    return response

def get_model_from_settings():
    # Changed to fetch google_model_name and default to gemma-3-27b-it
    return frappe.db.get_single_value("DoppioBot Settings", "google_model_name") or "models/gemma-3-27b-it"

def ensure_spanish(response: str) -> str:
    print(f"Respuesta original: {response}")
    if not isinstance(response, str):
        response = str(response)
    try:
        lang = detect(response)
        print(f"Idioma detectado: {lang}")
        if lang != "es":
            translator = Translator()
            translated = translator.translate(response, dest="es")
            return translated.text
        return response
    except Exception as e:
        print(f"Error en la detección de idioma: {e}")
        return "Lo siento, hubo un error al procesar tu solicitud."

@tool
def consultar_identificacion_sat(identificacion: str) -> str:
    """
    Consulta el nombre de un cliente en el SAT de Guatemala utilizando su NIT o CUI.
    Args:
        identificacion (str): NIT o CUI del cliente a consultar.
    Returns:
        str: Nombre del cliente si se encuentra, o un mensaje de error.
    """
    try:
        if len(identificacion) == 9:
            nombre_cliente = frappe.get_attr("fel.certificacion.consultar_sat_nit")(identificacion)
        elif len(identificacion) == 13:
            nombre_cliente = frappe.get_attr("fel.certificacion.llamar_servicio_web")(identificacion)
        else:
            return "failed: La identificación proporcionada no es válida. Debe ser un NIT (9 dígitos) o un CUI (13 dígitos)."
        return nombre_cliente
    except Exception as e:
        return f"Error al consultar la identificación en el SAT: {str(e)}"

@tool
def create_sales_order(order_data: str) -> str:
    """
    Create a new Sales Order in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `customer`: The name of the customer (mandatory).
    - `items`: A list of items, each with:
        - `item_code`: The item code (mandatory).
        - `qty`: Quantity (mandatory).
        - `rate`: Price per unit (mandatory).
    - `delivery_date`: (optional) Delivery date in "YYYY-MM-DD" format.
    - `taxes`: (optional) A list of taxes to apply.
    - `additional_notes`: (optional) Additional text that may contain "EXENTO" or "EXENTA".
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(order_data)
        if not data.get("customer"):
            return "failed: Missing required field 'customer'."
        if not data.get("items"):
            return "failed: Missing required field 'items'."
        fecha_actual = date.today()
        ultimo_dia_del_mes = calendar.monthrange(fecha_actual.year, fecha_actual.month)[1]
        fecha_ultimo_dia = date(fecha_actual.year, fecha_actual.month, ultimo_dia_del_mes)
        additional_notes = data.get("additional_notes", "").strip().upper()
        is_exento = "EXENTO" in additional_notes or "EXENTA" in additional_notes
        plantilla = ""
        if not is_exento:
            plantilla = frappe.get_value("Sales Taxes and Charges Template", {'is_default': 1}, "name") or ""
        print(f"Plantilla de impuestos: {plantilla}")
        data.setdefault("posting_date", fecha_actual)
        data.setdefault("delivery_date", fecha_ultimo_dia)
        data.setdefault("taxes_and_charges", plantilla) 
        items = []
        for item in data["items"]:
            if not item.get("item_code") or not item.get("qty") or not item.get("rate"):
                return "failed: Missing required fields in 'items' (item_code, qty, or rate)."
            items.append({
                "item_code": item["item_code"],
                "qty": item["qty"],
                "rate": item["rate"]
            })
        taxes = []
        if data.get("taxes") and not is_exento:
            for tax in data["taxes"]:
                if not tax.get("account_head") or not tax.get("rate"):
                    return "failed: Missing required fields in 'taxes' (account_head or rate)."
                taxes.append({
                    "charge_type": "On Net Total",
                    "account_head": tax["account_head"],
                    "rate": tax["rate"]
                })
        elif data.get("taxes_and_charges") and not is_exento:
            taxes = frappe.get_doc("Sales Taxes and Charges Template", data["taxes_and_charges"]).taxes
        order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": data["customer"],
            "items": items,
            "cost_center": data.get("cost_center"), # Corrected: use get to avoid KeyError
            "delivery_date": data.get("delivery_date"),
            "taxes_and_charges": data.get("taxes_and_charges"),
            "taxes": taxes,
        })
        order.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Sales Order: {str(e)}")
        return f"failed: {str(e)}"

@tool
def create_sales_invoice(invoice_data: str) -> str:
    """
    Create a new Sales Invoice in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `customer`: The name of the customer (mandatory).
    - `center_cost`: The name of the cost center.
    - `items`: A list of items, each with:
        - `item_code`: The item code (mandatory).
        - `qty`: Quantity (mandatory).
        - `rate`: Price per unit (mandatory).
    - `due_date`: (optional) Invoice due date in "YYYY-MM-DD" format.
    - `taxes`: (optional) A list of taxes to apply.
    - `fel_status`: (optional) Text indicating if the invoice is "CON FEL" or "SIN FEL".
    - `additional_notes`: (optional) Additional text that may contain "EXENTO" or "EXENTA".
    - `id_identificacion`: (optional) Identification type, must be "NIT" or "CUI".
    - `id_receptor_`: (optional) Receiver identification number, must be numeric.
    Returns "done" if successful, otherwise "failed".
    """
    try:
        if not invoice_data or not invoice_data.strip():
            return "failed: Empty or invalid JSON input."
        print(f"Input received: {invoice_data}")
        try:
            data = json.loads(invoice_data.strip())
        except json.JSONDecodeError as e:
            return f"failed: Invalid JSON format. Error: {str(e)}"
        print(f"Parsed data: {data}")
        if not data.get("customer"):
            return "failed: Missing required field 'customer'."
        if not data.get("items"):
            return "failed: Missing required field 'items'."
        for item in data["items"]:
            if not item.get("item_code") or not item.get("qty") or not item.get("rate"):
                return "failed: Missing required fields in 'items' (item_code, qty, or rate)."
        if data.get("id_identificacion") and data["id_identificacion"].upper() not in ["NIT", "CUI"]:
            return "failed: 'id_identificacion' must be 'NIT' or 'CUI'."
        if data.get("id_receptor_") and not str(data["id_receptor_"]).isdigit():
            return "failed: 'id_receptor_' must be a numeric value."
        customer_company = frappe.defaults.get_user_default("Company")
        company_config = frappe.get_doc("Company Configuration", {"company": customer_company})
        print(f"Company config: {company_config}")
        if company_config.default_fel_configuration:
            if not data.get("id_identificacion"):
                return "failed: Missing required field 'id_identificacion'."
            if not data.get("id_receptor_"):
                return "failed: Missing required field 'id_receptor_'."
        fecha_actual = date.today()
        ultimo_dia_del_mes = calendar.monthrange(fecha_actual.year, fecha_actual.month)[1]
        fecha_ultimo_dia = date(fecha_actual.year, fecha_actual.month, ultimo_dia_del_mes)
        additional_notes = data.get("additional_notes", "").strip().upper()
        is_exento = "EXENTO" in additional_notes or "EXENTA" in additional_notes
        plantilla = ""
        if not is_exento:
            plantilla = frappe.get_value("Sales Taxes and Charges Template", {'is_default': 1}, "name") or ""
        print(f"Plantilla de impuestos: {plantilla}")
        data.setdefault("posting_date", fecha_actual)
        data.setdefault("due_date", fecha_ultimo_dia)
        data.setdefault("taxes_and_charges", plantilla)
        data.setdefault("update_stock", 1)
        fel_status = data.get("fel_status", "").strip().upper()
        custom_fel = 0
        if fel_status == "CON FEL":
            custom_fel = 1
        invoice_doc_data = {
            "doctype": "Sales Invoice",
            "customer": data["customer"],
            "cost_center": data.get("center_cost", ""),
            "items": [],
            "due_date": data.get("due_date"),
            "taxes_and_charges": data.get("taxes_and_charges"),
            "custom_fel": custom_fel
        }
        if company_config.default_fel_configuration:
            invoice_doc_data.update({
                "vendedor": data.get("vendedor", frappe.session.user),
                "id_identificacion": data.get("id_identificacion"),
                "id_receptor_": data.get("id_receptor_")
            })
        for item_data in data["items"]:
            item_code = item_data["item_code"]
            qty = item_data["qty"]
            rate = item_data["rate"]
            # Placeholder for item-specific logic if needed in future
            invoice_doc_data["items"].append({
                "item_code": item_code,
                "qty": qty,
                "rate": rate
            })
        invoice = frappe.get_doc(invoice_doc_data)
        invoice.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Sales Invoice: {str(e)}")
        return f"failed: {str(e)}"

@tool
def create_purchase_invoice(invoice_data: str) -> str:
    """
    Create a new Purchase Invoice in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `supplier`: The name of the supplier (mandatory).
    - `items`: A list of items, each with:
        - `item_code`: The item code (mandatory).
        - `qty`: Quantity (mandatory).
        - `rate`: Price per unit (mandatory).
    - `bill_date`: (optional) Invoice bill date in "YYYY-MM-DD" format.
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(invoice_data)
        if not data.get("supplier"):
            return "failed: Missing required field 'supplier'."
        if not data.get("items"):
            return "failed: Missing required field 'items'."
        for item in data["items"]:
            if not item.get("item_code") or not item.get("qty") or not item.get("rate"):
                return "failed: Missing required fields in 'items' (item_code, qty, or rate)."
        data.setdefault("bill_date", date.today())
        data.setdefault("due_date", date.today())
        data.setdefault("update_stock", 1)
        invoice = frappe.get_doc({
            "doctype": "Purchase Invoice",
            "supplier": data["supplier"],
            "items": data["items"],
            "bill_date": data["bill_date"],
            "due_date": data["due_date"],
            "update_stock": data["update_stock"]
        })
        invoice.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Purchase Invoice: {str(e)}")
        return f"failed: {str(e)}"

@tool
def create_item(item_data: str) -> str:
    """
    Create a new Item in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `item_code`: The item code (mandatory).
    - `item_group`: The item group (mandatory).
    - `stock_uom`: Stock Unit of Measure (mandatory).
    - `standard_rate`: (optional) Standard selling rate.
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(item_data)
        if not data.get("item_code") or not data.get("item_group") or not data.get("stock_uom"):
            return "failed: Missing required fields (item_code, item_group, or stock_uom)."
        item = frappe.get_doc({
            "doctype": "Item",
            "item_code": data["item_code"],
            "item_group": data["item_group"],
            "stock_uom": data["stock_uom"],
            "standard_rate": data.get("standard_rate", 0)
        })
        item.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Item: {str(e)}")
        return f"failed: {str(e)}"

@tool
def create_customer(customer_data: str) -> str:
    """
    Create a new Customer in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `customer_name`: The name of the customer (mandatory).
    - `customer_group`: The customer group (mandatory, e.g., "Commercial", "Individual").
    - `customer_type`: (optional) "Company" or "Individual". Defaults to "Individual".
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(customer_data)
        if not data.get("customer_name") or not data.get("customer_group"):
            return "failed: Missing required fields (customer_name or customer_group)."
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": data["customer_name"],
            "customer_group": data["customer_group"],
            "customer_type": data.get("customer_type", "Individual")
        })
        customer.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Customer: {str(e)}")
        return f"failed: {str(e)}"

@tool
def create_suppliers(supplier_data: str) -> str:
    """
    Create a new Supplier in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `supplier_name`: The name of the supplier (mandatory).
    - `supplier_group`: The supplier group (mandatory, e.g., "Local", "Import").
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(supplier_data)
        if not data.get("supplier_name") or not data.get("supplier_group"):
            return "failed: Missing required fields (supplier_name or supplier_group)."
        supplier = frappe.get_doc({
            "doctype": "Supplier",
            "supplier_name": data["supplier_name"],
            "supplier_group": data["supplier_group"]
        })
        supplier.insert()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error creating Supplier: {str(e)}")
        return f"failed: {str(e)}"

@tool
def update_customers(customer_data: str) -> str:
    """
    Update an existing Customer in Frappe ERPNext.
    Expected input: JSON string with the following fields:
    - `customer_name`: The name of the customer to update (mandatory).
    - `fields_to_update`: A dictionary of fields to update, e.g., {"credit_limit": 5000}.
    Returns "done" if successful, otherwise "failed".
    """
    try:
        data = frappe.parse_json(customer_data)
        if not data.get("customer_name") or not data.get("fields_to_update"):
            return "failed: Missing required fields (customer_name or fields_to_update)."
        customer_name = data["customer_name"]
        fields_to_update = data["fields_to_update"]
        if not frappe.db.exists("Customer", customer_name):
            return f"failed: Customer {customer_name} not found."
        customer = frappe.get_doc("Customer", customer_name)
        customer.update(fields_to_update)
        customer.save()
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error updating Customer: {str(e)}")
        return f"failed: {str(e)}"

@tool
def delete_customers(customer_name: str) -> str:
    """
    Delete an existing Customer in Frappe ERPNext.
    Expected input: Name of the customer to delete (string).
    Returns "done" if successful, otherwise "failed".
    """
    try:
        if not frappe.db.exists("Customer", customer_name):
            return f"failed: Customer {customer_name} not found."
        frappe.delete_doc("Customer", customer_name)
        frappe.db.commit()
        return "done"
    except Exception as e:
        frappe.log_error(f"Error deleting Customer: {str(e)}")
        return f"failed: {str(e)}"

@tool
def get_info_customer(customer_name: str) -> str:
    """
    Get information about an existing Customer in Frappe ERPNext.
    Expected input: Name of the customer (string).
    Returns customer information as a JSON string if successful, otherwise "failed".
    """
    try:
        if not frappe.db.exists("Customer", customer_name):
            return f"failed: Customer {customer_name} not found."
        customer_info = frappe.get_doc("Customer", customer_name).as_json()
        return customer_info
    except Exception as e:
        frappe.log_error(f"Error getting Customer info: {str(e)}")
        return f"failed: {str(e)}"

@tool
def get_item_stats(item_code: str) -> str:
    """
    Get statistics for a specific item in Frappe ERPNext.
    Expected input: Item code (string).
    Returns item statistics as a JSON string if successful, otherwise "failed".
    """
    try:
        if not frappe.db.exists("Item", item_code):
            return f"failed: Item {item_code} not found."
        # Example: Get stock level and last sale price (customize as needed)
        stock_level = frappe.db.sql("""SELECT SUM(actual_qty) 
                                       FROM `tabStock Ledger Entry` 
                                       WHERE item_code = %s""", item_code)
        stock_level = stock_level[0][0] if stock_level and stock_level[0][0] is not None else 0
        
        last_sale_price = frappe.db.sql("""SELECT rate 
                                           FROM `tabSales Invoice Item` 
                                           WHERE item_code = %s 
                                           ORDER BY creation DESC LIMIT 1""", item_code)
        last_sale_price = last_sale_price[0][0] if last_sale_price else 0

        stats = {
            "item_code": item_code,
            "stock_level": stock_level,
            "last_sale_price": last_sale_price
        }
        return json.dumps(stats)
    except Exception as e:
        frappe.log_error(f"Error getting Item stats: {str(e)}")
        return f"failed: {str(e)}"

@tool
def get_sales_stats(period: str) -> str:
    """
    Get sales statistics for a given period (e.g., "last_month", "this_year").
    Expected input: Period string.
    Returns sales statistics as a JSON string if successful, otherwise "failed".
    """
    try:
        today = datetime.today()
        if period == "last_month":
            first_day_last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            last_day_last_month = today.replace(day=1) - timedelta(days=1)
            start_date = first_day_last_month.strftime("%Y-%m-%d")
            end_date = last_day_last_month.strftime("%Y-%m-%d")
        elif period == "this_year":
            start_date = today.replace(month=1, day=1).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
        else:
            return "failed: Invalid period specified. Use 'last_month' or 'this_year'."

        total_sales = frappe.db.sql("""SELECT SUM(grand_total) 
                                        FROM `tabSales Invoice` 
                                        WHERE posting_date BETWEEN %s AND %s 
                                        AND docstatus = 1""", (start_date, end_date))
        total_sales = total_sales[0][0] if total_sales and total_sales[0][0] is not None else 0

        stats = {
            "period": period,
            "total_sales": total_sales
        }
        return json.dumps(stats)
    except Exception as e:
        frappe.log_error(f"Error getting Sales stats: {str(e)}")
        return f"failed: {str(e)}"

