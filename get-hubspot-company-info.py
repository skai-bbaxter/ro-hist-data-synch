# This code returns ALL Company properties from HubSpot

import os
import argparse
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# === Helper for datetime serialization ===
def custom_json_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


# === Helper function to find contact with matching email pattern ===
def find_contact_with_email_pattern(contacts, base_url, headers, source_name="contacts"):
    """
    Search through contacts for one with email matching 'hubspot+*@skaivision.net' pattern.
    
    Args:
        contacts: List of contact association objects (with 'id' field)
        base_url: HubSpot API base URL
        headers: Request headers with authentication
        source_name: Name of the source (e.g., "Deal contacts", "Company contacts") for logging
    
    Returns:
        dict: Matching contact data or None if not found
    """
    matching_contact_data = None
    
    print(f"\n🔍 Searching through {len(contacts)} {source_name} for email pattern 'hubspot+*@skaivision.net'...")
    
    for contact in contacts:
        contact_id = contact["id"]
        
        # Get the contact details to retrieve the email
        # Try multiple email properties in case the primary one is empty
        contact_url = f"{base_url}/crm/v3/objects/contacts/{contact_id}?properties=email,hs_email_address,firstname,lastname"
        contact_response = requests.get(contact_url, headers=headers)
        
        if contact_response.status_code == 200:
            contact_data = contact_response.json()
            properties = contact_data.get("properties", {})
            
            # Try multiple email fields
            email = properties.get("email", "") or properties.get("hs_email_address", "")
            
            # Strip whitespace and convert to lowercase for comparison
            email_clean = email.strip().lower() if email else ""
            
            # Debug: Print all emails found
            if email_clean:
                print(f"   Contact ID {contact_id}: {email_clean}")
            
            # Check if email matches pattern: starts with "hubspot+" and ends with "@skaivision.net" (case-insensitive)
            if email_clean.startswith("hubspot+") and email_clean.endswith("@skaivision.net"):
                # Handle None values by converting to empty string
                firstname = properties.get("firstname") or ""
                lastname = properties.get("lastname") or ""
                
                matching_contact_data = {
                    "id": contact_id,
                    "email": email.strip() if email else email,  # Keep original email format
                    "firstname": firstname,
                    "lastname": lastname,
                    "full_data": contact_data,
                    "source": source_name
                }
                break
    
    return matching_contact_data


def get_company_info(company_identifier, hubspot_token):
    """
    Retrieve all properties for a company from HubSpot by company ID or company name.

    company_identifier can be either:
    - A HubSpot company ID (numeric string, e.g. "12345")
    - A company name (exact match in HubSpot, e.g. "Acme Corp")

    Args:
        company_identifier (str): Either the HubSpot company ID or the company name
        hubspot_token (str): The HubSpot access token for authentication

    Returns:
        dict: The full company data with all properties
    """
    BASE_URL = "https://api.hubapi.com"
    HEADERS = {
        "Authorization": f"Bearer {hubspot_token}",
        "Content-Type": "application/json"
    }

    company_id = None

    # === Step 1: Resolve to company_id (from ID or by searching by name) ===
    if company_identifier.strip().isdigit():
        company_id = company_identifier.strip()
        print(f"✔ Using Company ID: {company_id}")
    else:
        company_name = company_identifier.strip()
        search_url = f"{BASE_URL}/crm/v3/objects/companies/search"
        search_payload = json.dumps({
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "name",
                            "operator": "EQ",
                            "value": company_name
                        }
                    ]
                }
            ],
            "properties": ["name", "domain", "skai_org_id", "createdate"],
            "limit": 1
        })

        try:
            search_response = requests.post(search_url, headers=HEADERS, data=search_payload)
            search_response.raise_for_status()
            search_data = search_response.json()

            if not search_data.get("results"):
                raise Exception(f"No company found with name '{company_name}'")

            company_id = search_data["results"][0]["id"]
            print(f"✔ Found Company: {company_name}, ID: {company_id}")

        except Exception as e:
            print(f"❌ Error searching for company: {e}")
            raise SystemExit()


    # === Step 2: Fetch all property names for companies ===
    try:
        properties_url = f"{BASE_URL}/crm/v3/properties/companies"
        properties_response = requests.get(properties_url, headers=HEADERS)
        properties_response.raise_for_status()
        properties_data = properties_response.json()
        
        all_property_names = [prop["name"] for prop in properties_data.get("results", [])]
        print(f"✔ Retrieved {len(all_property_names)} company property names")

    except Exception as e:
        print(f"❌ Error fetching company properties: {e}")
        raise SystemExit()


    # === Step 3: Retrieve the full company record by ID with all properties ===
    try:
        company_url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}"
        params = {
            "archived": "false",
            "properties": ",".join(all_property_names)
        }
        company_response = requests.get(company_url, headers=HEADERS, params=params)
        company_response.raise_for_status()
        company_data = company_response.json()
        hs_name = company_data["properties"]["name"]
        hs_id = company_data["id"]
        print(f"\nname:{hs_name}  id:{hs_id}")
        
        print("✔ Full Company Data:")
        print(json.dumps(company_data, indent=4, default=custom_json_serializer))
        
    except Exception as e:
        print(f"❌ Error retrieving full company data: {e}")
        raise SystemExit()


    # === Step 4: Get deals associated with the company ===
    try:
        company_deals_url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}/associations/deals"
        deals_response = requests.get(company_deals_url, headers=HEADERS)
        deals_response.raise_for_status()
        deals = deals_response.json().get("results", [])
        
        if not deals:
            print("\n⚠ No deals found associated with this company")
            return company_data
        
        print(f"\n✔ Found {len(deals)} deal(s) associated with the company")
        
        # Get the first deal ID
        deal_id = deals[0]['id']
        print(f"✔ Using Deal ID: {deal_id}")
        
    except Exception as e:
        print(f"❌ Error retrieving deals: {e}")
        return company_data


    # === Step 5: Get the deal owner ID ===
    try:
        deal_owner_url = f"{BASE_URL}/crm/v3/objects/deals/{deal_id}?properties=hubspot_owner_id"
        deal_owner_response = requests.get(deal_owner_url, headers=HEADERS)
        deal_owner_response.raise_for_status()
        deal_owner_data = deal_owner_response.json()
        hubspot_owner_id = deal_owner_data.get("properties", {}).get("hubspot_owner_id")
        
        if not hubspot_owner_id:
            print("\n⚠ No owner assigned to this deal")
            return company_data
        
        print(f"✔ Deal Owner ID: {hubspot_owner_id}")
        
    except Exception as e:
        print(f"❌ Error retrieving deal owner ID: {e}")
        return company_data


    # === Step 6: Get the owner's name ===
    try:
        owner_url = f"{BASE_URL}/crm/v3/owners/{hubspot_owner_id}"
        owner_response = requests.get(owner_url, headers=HEADERS)
        owner_response.raise_for_status()
        owner_data = owner_response.json()
        
        # Owner name can be in different fields, try common ones
        owner_name = (
            owner_data.get("firstName", "") + " " + owner_data.get("lastName", "")
        ).strip()
        
        if not owner_name:
            owner_name = owner_data.get("email", "Unknown")
        
        print(f"\n✔ Deal Owner Name: {owner_name}")
        
    except Exception as e:
        print(f"❌ Error retrieving owner name: {e}")
        return company_data


    # === Step 7: Get contacts associated with the deal ===
    try:
        deal_contacts_url = f"{BASE_URL}/crm/v3/objects/deals/{deal_id}/associations/contacts"
        deal_contacts_response = requests.get(deal_contacts_url, headers=HEADERS)
        deal_contacts_response.raise_for_status()
        deal_contacts = deal_contacts_response.json().get("results", [])
        
        if not deal_contacts:
            print(f"\n⚠ No contacts found associated with deal {deal_id}")
        else:
            print(f"\n✔ Found {len(deal_contacts)} contact(s) associated with the deal")
            print(f"   Contact IDs: {[c['id'] for c in deal_contacts]}")
            
            # Check deal contacts for matching email pattern
            matching_deal_contact = find_contact_with_email_pattern(
                deal_contacts, BASE_URL, HEADERS, "Deal contact(s)"
            )
            
            if matching_deal_contact:
                # Safely handle None values by converting to empty strings
                firstname = matching_deal_contact.get("firstname") or ""
                lastname = matching_deal_contact.get("lastname") or ""
                contact_name = (firstname + " " + lastname).strip()
                if not contact_name:
                    contact_name = "Unknown"
                
                print(f"\n✔ Found matching Deal contact:")
                print(f"   Contact ID: {matching_deal_contact['id']}")
                print(f"   Name: {contact_name}")
                print(f"   Email: {matching_deal_contact['email']}")
                print(f"   Source: {matching_deal_contact['source']}")
                print(f"\n✔ Full Deal Contact Data:")
                print(json.dumps(matching_deal_contact["full_data"], indent=4, default=custom_json_serializer))
            else:
                print(f"\n⚠ No Deal contact found with email matching pattern 'hubspot+*@skaivision.net'")
        
    except Exception as e:
        print(f"❌ Error retrieving deal contacts: {e}")
        import traceback
        traceback.print_exc()


    # === Step 8: Get contacts associated with the company ===
    try:
        company_contacts_url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}/associations/contacts"
        contacts_response = requests.get(company_contacts_url, headers=HEADERS)
        contacts_response.raise_for_status()
        contacts = contacts_response.json().get("results", [])
        
        if not contacts:
            print("\n⚠ No contacts found associated with this company")
            contacts = []  # Set to empty list to continue processing
        else:
            print(f"\n✔ Found {len(contacts)} contact(s) associated with the company")
            print(f"   Contact IDs: {[c['id'] for c in contacts]}")
        
    except Exception as e:
        print(f"❌ Error retrieving contacts: {e}")
        import traceback
        traceback.print_exc()
        contacts = []  # Set to empty list to continue processing


    # === Step 9: Find company contact with email matching pattern ===
    try:
        # Check company contacts for matching email pattern
        matching_company_contact = find_contact_with_email_pattern(
            contacts, BASE_URL, HEADERS, "Company contact(s)"
        )
        
        if matching_company_contact:
            # Safely handle None values by converting to empty strings
            firstname = matching_company_contact.get("firstname") or ""
            lastname = matching_company_contact.get("lastname") or ""
            contact_name = (firstname + " " + lastname).strip()
            if not contact_name:
                contact_name = "Unknown"
            
            print(f"\n✔ Found matching Company contact:")
            print(f"   Contact ID: {matching_company_contact['id']}")
            print(f"   Name: {contact_name}")
            print(f"   Email: {matching_company_contact['email']}")
            print(f"   Source: {matching_company_contact['source']}")
            print(f"\n✔ Full Company Contact Data:")
            print(json.dumps(matching_company_contact["full_data"], indent=4, default=custom_json_serializer))
        else:
            print("\n⚠ No Company contact found with email matching pattern 'hubspot+*@skaivision.net'")
            print("   (Checked all contacts associated with the company)")
        
    except Exception as e:
        print(f"❌ Error searching for matching company contact: {e}")
        import traceback
        traceback.print_exc()
        return company_data
    
    return company_data


def main():
    # Load environment variables from .env file
    load_dotenv()
    
    # Get HubSpot access token from .env file
    hubspot_token = os.getenv("HUBSPOT_PROD_TOKEN")
    if not hubspot_token:
        raise ValueError("HUBSPOT_PROD_TOKEN not found in .env file")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Retrieve all company properties from HubSpot by company ID or company name",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 12345678901
  %(prog)s "Stokes Toyota Hilton Head"
  %(prog)s "Acme Corporation"

Pass a numeric Company ID (e.g. from HubSpot URL) or an exact company name.
        """
    )
    parser.add_argument(
        "company",
        type=str,
        metavar="COMPANY_ID_OR_NAME",
        help="HubSpot company ID (numeric) or company name (exact match as in HubSpot)"
    )

    args = parser.parse_args()

    # Get company information
    get_company_info(args.company, hubspot_token)


## MAIN ##
if __name__ == "__main__":
    main()
